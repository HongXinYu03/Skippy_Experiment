import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdir, writeFile } from 'node:fs/promises'
import { parseModel, validateMesh } from '../src/modelImport.ts'

const vertices = [
  [-1, -1, -1],
  [1, -1, -1],
  [1, 1, -1],
  [-1, 1, -1],
  [-1, -1, 1],
  [1, -1, 1],
  [1, 1, 1],
  [-1, 1, 1],
]
const faces = [
  [0, 2, 1],
  [0, 3, 2],
  [4, 5, 6],
  [4, 6, 7],
  [0, 1, 5],
  [0, 5, 4],
  [3, 7, 6],
  [3, 6, 2],
  [0, 4, 7],
  [0, 7, 3],
  [1, 2, 6],
  [1, 6, 5],
]

function ply(binary = false): ArrayBuffer {
  const header = new TextEncoder().encode(
    `ply\nformat ${binary ? 'binary_little_endian' : 'ascii'} 1.0\nelement vertex 8\nproperty float x\nproperty float y\nproperty float z\nelement face 12\nproperty list uchar int vertex_indices\nend_header\n`,
  )
  if (!binary)
    return new TextEncoder().encode(
      new TextDecoder().decode(header) +
        vertices.map((p) => p.join(' ')).join('\n') +
        '\n' +
        faces.map((f) => `3 ${f.join(' ')}`).join('\n'),
    ).buffer
  const bytes = new Uint8Array(header.length + vertices.length * 12 + faces.length * 13)
  bytes.set(header)
  const view = new DataView(bytes.buffer)
  let offset = header.length
  for (const p of vertices)
    for (const value of p) {
      view.setFloat32(offset, value, true)
      offset += 4
    }
  for (const f of faces) {
    view.setUint8(offset++, 3)
    for (const index of f) {
      view.setInt32(offset, index, true)
      offset += 4
    }
  }
  return bytes.buffer
}

function glb(): ArrayBuffer {
  const positions = new Float32Array(vertices.flat())
  const indices = new Uint16Array(faces.flat())
  const binary = new Uint8Array(positions.byteLength + indices.byteLength)
  binary.set(new Uint8Array(positions.buffer))
  binary.set(new Uint8Array(indices.buffer), positions.byteLength)
  const document = {
    asset: { version: '2.0' },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [
      { translation: [7, 3, 2], children: [1, 2] },
      { mesh: 0, translation: [-2, 0, 0], scale: [-1, 2, 1] },
      { mesh: 0, translation: [2, 0, 0] },
    ],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1, material: 0 }] }],
    materials: [{ pbrMetallicRoughness: { baseColorFactor: [0.2, 0.6, 0.8, 1] } }],
    buffers: [{ byteLength: binary.byteLength }],
    bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: positions.byteLength },
      { buffer: 0, byteOffset: positions.byteLength, byteLength: indices.byteLength },
    ],
    accessors: [
      {
        bufferView: 0,
        componentType: 5126,
        count: vertices.length,
        type: 'VEC3',
        min: [-1, -1, -1],
        max: [1, 1, 1],
      },
      { bufferView: 1, componentType: 5123, count: indices.length, type: 'SCALAR' },
    ],
  }
  const text = new TextEncoder().encode(JSON.stringify(document))
  const jsonLength = Math.ceil(text.length / 4) * 4
  const binaryLength = Math.ceil(binary.byteLength / 4) * 4
  const buffer = new ArrayBuffer(12 + 8 + jsonLength + 8 + binaryLength)
  const data = new DataView(buffer),
    bytes = new Uint8Array(buffer)
  data.setUint32(0, 0x46546c67, true)
  data.setUint32(4, 2, true)
  data.setUint32(8, buffer.byteLength, true)
  data.setUint32(12, jsonLength, true)
  data.setUint32(16, 0x4e4f534a, true)
  bytes.fill(32, 20, 20 + jsonLength)
  bytes.set(text, 20)
  data.setUint32(20 + jsonLength, binaryLength, true)
  data.setUint32(24 + jsonLength, 0x004e4942, true)
  bytes.set(binary, 28 + jsonLength)
  return buffer
}

for (const binary of [false, true]) {
  test(`imports ${binary ? 'binary' : 'ASCII'} PLY and normalizes its surface`, async () => {
    const mesh = await parseModel(ply(binary), binary ? 'cube.PLY' : 'cube.ply')
    assert.equal(mesh.faces.length, 12)
    assert.equal(mesh.vertices.length, 8)
    assert.equal(Math.max(...mesh.vertices.flat()), 1.5)
    assert.equal(Math.min(...mesh.vertices.flat()), -1.5)
    validateMesh(JSON.parse(JSON.stringify(mesh)))
  })
}

test('GLB bakes parent/child transforms, instances, mirrored winding and base colors', async () => {
  const mesh = await parseModel(glb(), 'two-cubes.glb')
  assert.equal(mesh.faces.length, 24)
  assert.equal(mesh.vertices.length, 16)
  assert.deepEqual(mesh.colors![0], [0.2, 0.6, 0.8])
  assert.equal(Math.max(...mesh.vertices.map((p) => p[0])), 1.5)
  assert.equal(Math.min(...mesh.vertices.map((p) => p[0])), -1.5)
  assert.equal(Math.max(...mesh.vertices.map((p) => p[1])), 1)
  // The first component is mirrored; its faces still point outwards.
  const first = mesh.faces[0]!.map((i) => mesh.vertices[i]!)
  const ab = first[1]!.map((v, i) => v - first[0]![i]!)
  const ac = first[2]!.map((v, i) => v - first[0]![i]!)
  assert.ok(ab[0]! * ac[1]! - ab[1]! * ac[0]! < 0)
  validateMesh(JSON.parse(JSON.stringify(mesh)))
})

test('rejects point clouds, malformed files, unsupported formats and invalid scene geometry', async () => {
  const cloud = new TextEncoder().encode(
    'ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\nend_header\n0 0 0\n1 0 0\n0 1 0\n',
  )
  await assert.rejects(parseModel(cloud.buffer, 'cloud.ply'), /点云/)
  await assert.rejects(parseModel(new ArrayBuffer(0), 'broken.glb'))
  await assert.rejects(parseModel(ply(), 'cube.obj'), /请选择/)
  assert.throws(
    () =>
      validateMesh({
        vertices: [
          [0, 0, 0],
          [1, 0, 0],
          [2, 0, 0],
        ],
        faces: [[0, 1, 2]],
      }),
    /退化/,
  )
  assert.throws(
    () =>
      validateMesh({
        vertices: [
          [0, 0, 0],
          [1, 0, 0],
          [0, 1, 0],
        ],
        faces: [[0, 1, 99]],
      }),
    /无效/,
  )
})

test('writes small local fixtures for browser import checks', async () => {
  const directory = new URL('../node_modules/.tmp/model-import/', import.meta.url)
  await mkdir(directory, { recursive: true })
  await writeFile(new URL('cube.ply', directory), new Uint8Array(ply()))
  await writeFile(new URL('cube-binary.ply', directory), new Uint8Array(ply(true)))
  await writeFile(new URL('two-cubes.glb', directory), new Uint8Array(glb()))
})
