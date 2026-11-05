import * as THREE from 'three'
import type { MeshData, V3 } from './types'

export const MAX_MODEL_BYTES = 64 * 1024 * 1024
export const MAX_MESH_ELEMENTS = 100_000

export function validateMesh(mesh: MeshData): void {
  if (
    !mesh ||
    !Array.isArray(mesh.vertices) ||
    !Array.isArray(mesh.faces) ||
    mesh.vertices.length < 3 ||
    !mesh.faces.length ||
    mesh.vertices.length > MAX_MESH_ELEMENTS ||
    mesh.faces.length > MAX_MESH_ELEMENTS ||
    mesh.vertices.some((p) => !Array.isArray(p) || p.length !== 3 || !p.every(Number.isFinite)) ||
    mesh.faces.some(
      (face) =>
        !Array.isArray(face) ||
        face.length !== 3 ||
        face.some((i) => !Number.isInteger(i) || i < 0 || i >= mesh.vertices.length),
    ) ||
    (mesh.colors &&
      (mesh.colors.length !== mesh.vertices.length ||
        mesh.colors.some(
          (p) =>
            !Array.isArray(p) ||
            p.length !== 3 ||
            !p.every((v) => Number.isFinite(v) && v >= 0 && v <= 1),
        )))
  )
    throw new Error('网格数据无效，或超过 10 万顶点 / 三角面限制。')
  const bounds = new THREE.Box3().setFromPoints(mesh.vertices.map((p) => new THREE.Vector3(...p)))
  const extent = Math.max(...bounds.getSize(new THREE.Vector3()).toArray())
  if (!Number.isFinite(extent) || extent <= 0) throw new Error('模型没有有效的几何尺寸。')
  const a = new THREE.Vector3(),
    b = new THREE.Vector3(),
    c = new THREE.Vector3()
  for (const face of mesh.faces) {
    a.fromArray(mesh.vertices[face[0]]!)
    b.fromArray(mesh.vertices[face[1]]!).sub(a)
    c.fromArray(mesh.vertices[face[2]]!).sub(a)
    if (b.cross(c).length() <= extent * extent * 1e-14)
      throw new Error('网格包含退化三角面，请先清理模型。')
  }
}

export function meshGeometry(mesh: MeshData): THREE.BufferGeometry {
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(mesh.vertices.flat(), 3))
  geometry.setIndex(mesh.faces.flat())
  if (mesh.colors)
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(mesh.colors.flat(), 3))
  geometry.computeVertexNormals()
  geometry.computeBoundingSphere()
  return geometry
}

function disposeModel(root: THREE.Object3D): void {
  const textures = new Set<THREE.Texture>()
  root.traverse((object) => {
    if (!(object instanceof THREE.Mesh)) return
    object.geometry.dispose()
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      for (const value of Object.values(material))
        if (value instanceof THREE.Texture) textures.add(value)
      material.dispose()
    }
  })
  textures.forEach((texture) => {
    const source = texture.source.data
    if (typeof ImageBitmap !== 'undefined' && source instanceof ImageBitmap) source.close()
    texture.dispose()
  })
}

/** Bake the static glTF pose and all node/instance transforms into one mesh. */
function collect(root: THREE.Object3D): MeshData {
  const vertices: V3[] = [],
    colors: V3[] = [],
    faces: MeshData['faces'] = []
  const point = new THREE.Vector3(),
    color = new THREE.Color()
  root.updateMatrixWorld(true)
  root.traverse((object) => {
    if (object instanceof THREE.SkinnedMesh) object.skeleton.update()
  })
  root.traverse((object) => {
    if (!(object instanceof THREE.Mesh)) return
    if (object instanceof THREE.InstancedMesh && object.count > MAX_MESH_ELEMENTS)
      throw new Error('模型实例数量过多，请先简化模型。')
    const geometry = object.geometry as THREE.BufferGeometry
    const positions = geometry.getAttribute('position')
    if (!positions) return
    if (positions.count > MAX_MESH_ELEMENTS * 3) throw new Error('模型顶点数量过多，请先简化模型。')
    const index = geometry.getIndex()
    const count = index?.count ?? positions.count
    const instances = object instanceof THREE.InstancedMesh ? object.count : 1
    if (count % 3 !== 0) throw new Error('模型必须包含完整的三角面。')
    if (faces.length + (count / 3) * instances > MAX_MESH_ELEMENTS)
      throw new Error('模型超过 10 万三角面，请简化后导入。')
    const matrix = new THREE.Matrix4(),
      instance = new THREE.Matrix4()
    for (let copy = 0; copy < instances; copy++) {
      matrix.copy(object.matrixWorld)
      if (object instanceof THREE.InstancedMesh) {
        object.getMatrixAt(copy, instance)
        matrix.multiply(instance)
      }
      // A negative determinant reverses winding, which is corrected on baking.
      const mirrored = matrix.determinant() < 0
      const offset = vertices.length
      const attribute = geometry.getAttribute('color')
      const material = (
        Array.isArray(object.material) ? object.material[0] : object.material
      ) as THREE.MeshStandardMaterial
      for (let i = 0; i < positions.count; i++) {
        object.getVertexPosition(i, point).applyMatrix4(matrix)
        vertices.push(point.toArray() as V3)
        color.setRGB(1, 1, 1)
        if (attribute) color.fromBufferAttribute(attribute, i)
        if (material?.color) color.multiply(material.color)
        colors.push(color.toArray() as V3)
      }
      for (let i = 0; i < count; i += 3) {
        const a = offset + (index ? index.getX(i) : i)
        const b = offset + (index ? index.getX(i + 1) : i + 1)
        const c = offset + (index ? index.getX(i + 2) : i + 2)
        faces.push(mirrored ? [a, c, b] : [a, b, c])
      }
    }
  })
  return { vertices, faces, colors }
}

function normalize(source: MeshData): MeshData {
  if (!source.faces.length) throw new Error('文件中没有三角面。纯点云 PLY 请先重建为带面的网格。')
  const bounds = new THREE.Box3().setFromPoints(source.vertices.map((p) => new THREE.Vector3(...p)))
  const center = bounds.getCenter(new THREE.Vector3())
  const extent = Math.max(...bounds.getSize(new THREE.Vector3()).toArray())
  if (!Number.isFinite(extent) || extent <= 0) throw new Error('模型包含无效坐标或没有有效尺寸。')
  // Keep the default camera, solver tolerances and imported files in a useful scale.
  const factor = 3 / extent
  const vertices: V3[] = [],
    colors: V3[] = [],
    faces: MeshData['faces'] = []
  const indices = new Map<string, number>()
  const remap = source.vertices.map((p, i) => {
    const point: V3 = [
      (p[0] - center.x) * factor,
      (p[1] - center.y) * factor,
      (p[2] - center.z) * factor,
    ]
    if (!point.every(Number.isFinite)) throw new Error('模型包含无效坐标。')
    const color = source.colors?.[i]
    // Include colors in the key so per-face color boundaries survive welding.
    const key = point.map((v) => v.toFixed(10)).join(',') + (color ? `/${color.join(',')}` : '')
    let next = indices.get(key)
    if (next === undefined) {
      next = vertices.length
      indices.set(key, next)
      vertices.push(point)
      if (color) colors.push(color)
    }
    return next
  })
  const a = new THREE.Vector3(),
    b = new THREE.Vector3(),
    c = new THREE.Vector3()
  for (const face of source.faces) {
    const mapped = face.map((i) => remap[i]) as [number, number, number]
    if (mapped.some((i) => i === undefined)) throw new Error('模型的三角面索引越界。')
    a.fromArray(vertices[mapped[0]]!)
    b.fromArray(vertices[mapped[1]]!).sub(a)
    c.fromArray(vertices[mapped[2]]!).sub(a)
    if (b.cross(c).length() > 9e-14) faces.push(mapped)
  }
  const mesh = { vertices, faces, ...(colors.length ? { colors } : {}) }
  validateMesh(mesh)
  return mesh
}

export async function parseModel(data: ArrayBuffer, filename: string): Promise<MeshData> {
  if (data.byteLength > MAX_MODEL_BYTES) throw new Error('模型文件超过 64 MB，请简化后导入。')
  const extension = filename.split('.').pop()?.toLowerCase()
  if (extension === 'ply') {
    const { PLYLoader } = await import('three/addons/loaders/PLYLoader.js')
    const geometry = new PLYLoader().parse(data)
    const root = new THREE.Mesh(geometry)
    try {
      // PLYLoader leaves pure point clouds unindexed; they are not triangles.
      if (!geometry.index?.count) throw new Error('PLY 没有面数据。纯点云请先重建为三角网格。')
      return normalize(collect(root))
    } finally {
      disposeModel(root)
    }
  }
  if (extension !== 'glb') throw new Error('请选择 .ply 或 .glb 格式的模型。')
  const manager = new THREE.LoadingManager()
  manager.setURLModifier((url) => {
    if (url.startsWith('blob:') || url.startsWith('data:')) return url
    throw new Error('GLB 引用了外部资源，请导出包含内嵌资源的 GLB 文件。')
  })
  let root: THREE.Group | undefined
  try {
    const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js')
    const gltf = await new GLTFLoader(manager).parseAsync(data, '')
    root = gltf.scene
    return normalize(collect(root))
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    if (/draco|meshopt|ktx2/i.test(detail))
      throw new Error('此 GLB 使用了压缩扩展，请导出不含 Draco / Meshopt / KTX2 压缩的 GLB。')
    throw error
  } finally {
    if (root) disposeModel(root)
  }
}
