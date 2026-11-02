<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { TransformControls } from 'three/addons/controls/TransformControls.js'
import type { Shape, CameraData, CurveRecord, Result, V2, V3, Mode } from '../types'

const props = defineProps<{
  objects: Shape[]
  curves: CurveRecord[]
  selectedObject: string
  selectedCurve: string
  mode: Mode
  preview: Result | null
  stroke: V2[]
  showRaw: boolean
  showCandidates: boolean
  xray: boolean
  samples: boolean
  transformMode: 'translate' | 'rotate' | 'scale'
}>()
const emit = defineEmits<{
  begin: [point: V2]
  move: [point: V2]
  finish: []
  pick: [id: string]
  skip: [point: V2]
  guide: [point: V3, normal: V3]
  change: [shape: Shape]
  history: []
  camera: []
}>()
const host = ref<HTMLDivElement>()
const overlay = ref<HTMLCanvasElement>()
const failed = ref('')
let renderer: THREE.WebGLRenderer,
  scene: THREE.Scene,
  camera: THREE.PerspectiveCamera,
  controls: OrbitControls,
  transform: TransformControls
let resize: ResizeObserver,
  frame = 0,
  drawing = false,
  width = 1,
  height = 1
const meshes = new Map<string, THREE.Mesh>()
const curveLayer = new THREE.Group(),
  debugLayer = new THREE.Group()
const raycaster = new THREE.Raycaster()
let disposed = false

function snapshot(): CameraData {
  camera.updateMatrixWorld()
  return {
    world: camera.matrixWorld.toArray(),
    projection: camera.projectionMatrix.toArray(),
    width,
    height,
  }
}
function setCamera(data: CameraData) {
  if (!camera) return
  const matrix = new THREE.Matrix4().fromArray(data.world)
  matrix.decompose(camera.position, camera.quaternion, camera.scale)
  camera.projectionMatrix.fromArray(data.projection)
  camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert()
  const target = camera.position
    .clone()
    .add(
      new THREE.Vector3(0, 0, -1)
        .applyQuaternion(camera.quaternion)
        .multiplyScalar(camera.position.length()),
    )
  controls.target.copy(target)
  camera.updateMatrixWorld()
  controls.update()
}
function reset() {
  camera.position.set(5, 3.3, 7)
  controls.target.set(0, 0, 0)
  camera.aspect = width / height
  camera.fov = 45
  camera.updateProjectionMatrix()
  controls.update()
}
function project(point: V3): V2 {
  const v = new THREE.Vector3(...point).project(camera)
  return [((v.x + 1) * width) / 2, ((1 - v.y) * height) / 2]
}
function clear(group: THREE.Group) {
  while (group.children.length) {
    const child = group.children[0] as THREE.Mesh
    group.remove(child)
    child.geometry?.dispose()
    const material = child.material
    if (Array.isArray(material)) material.forEach((m) => m.dispose())
    else material?.dispose()
  }
}
function updateObjects() {
  if (!scene) return
  for (const [id, mesh] of meshes) {
    if (!props.objects.some((o) => o.id === id)) {
      transform.detach()
      scene.remove(mesh)
      mesh.geometry.dispose()
      ;(mesh.material as THREE.Material).dispose()
      meshes.delete(id)
    }
  }
  for (const object of props.objects) {
    let mesh = meshes.get(object.id)
    if (!mesh) {
      const geometry =
        object.kind === 'sphere'
          ? new THREE.SphereGeometry(1, 64, 48)
          : object.kind === 'box'
            ? new THREE.BoxGeometry(1, 1, 1)
            : new THREE.TetrahedronGeometry(1)
      mesh = new THREE.Mesh(
        geometry,
        new THREE.MeshStandardMaterial({ roughness: 0.78, metalness: 0.02 }),
      )
      mesh.userData.id = object.id
      mesh.castShadow = true
      mesh.receiveShadow = true
      meshes.set(object.id, mesh)
      scene.add(mesh)
    }
    mesh.position.fromArray(object.position)
    mesh.quaternion.fromArray(object.quaternion)
    mesh.scale.fromArray(
      object.kind === 'box' ? object.size : [object.size[0], object.size[0], object.size[0]],
    )
    mesh.visible = object.visible !== false
    const material = mesh.material as THREE.MeshStandardMaterial
    material.color.set(
      object.guide ? '#b9b9b9' : object.id === props.selectedObject ? '#a9a9a9' : '#c5c5c5',
    )
    material.transparent = object.guide || !object.enabled
    material.opacity = !object.enabled ? 0.24 : object.guide ? 0.46 : 1
    material.wireframe = object.guide
  }
  const selected = meshes.get(props.selectedObject)
  if (selected && props.mode === 'edit') transform.attach(selected)
  else transform.detach()
}
function line(points: V3[], color: string, group: THREE.Group, opacity = 1, depthTest = true) {
  if (points.length < 2) return
  const geometry = new THREE.BufferGeometry().setFromPoints(
    points.map((p) => new THREE.Vector3(...p)),
  )
  const material = new THREE.LineBasicMaterial({
    color,
    transparent: opacity < 1,
    opacity,
    depthTest,
  })
  group.add(new THREE.Line(geometry, material))
}
function updateCurves() {
  if (!scene) return
  clear(curveLayer)
  clear(debugLayer)
  for (const curve of props.curves) {
    const selected = curve.id === props.selectedCurve
    line(
      curve.result.points,
      selected ? '#303030' : '#a6a6a6',
      curveLayer,
      selected ? 1 : 0.6,
      !props.xray,
    )
    if (props.xray && selected) line(curve.result.points, '#303030', curveLayer, 0.18, false)
  }
  const result = props.preview || props.curves.find((c) => c.id === props.selectedCurve)?.result
  if (result) {
    if (props.preview) line(result.points, '#303030', debugLayer, 0.7, !props.xray)
    if (props.showRaw) line(result.raw_points, '#828282', debugLayer, 0.65)
    if (props.showCandidates) {
      const points = result.debug.candidates.flat().map((c) => new THREE.Vector3(...c.point))
      const geometry = new THREE.BufferGeometry().setFromPoints(points)
      debugLayer.add(
        new THREE.Points(
          geometry,
          new THREE.PointsMaterial({ color: '#777777', size: 0.035, depthTest: !props.xray }),
        ),
      )
    }
  }
}
function paint() {
  const canvas = overlay.value!
  const ctx = canvas.getContext('2d')!
  const ratio = window.devicePixelRatio || 1
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
  ctx.clearRect(0, 0, width, height)
  if (props.stroke.length > 1) {
    ctx.strokeStyle = '#303030'
    ctx.lineWidth = 2.5
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    ctx.beginPath()
    props.stroke.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)))
    ctx.stroke()
  }
  if (props.samples) {
    const result = props.preview || props.curves.find((c) => c.id === props.selectedCurve)?.result
    if (result) {
      const projected = result.raw_points.map(project)
      projected.forEach(([x, y], i) => {
        ctx.fillStyle = result.debug.on[i] ? '#828282' : '#b5b5b5'
        ctx.beginPath()
        ctx.arc(x, y, 3, 0, Math.PI * 2)
        ctx.fill()
      })
    }
  }
}
function position(event: PointerEvent): V2 {
  const bounds = host.value!.getBoundingClientRect()
  return [event.clientX - bounds.left, event.clientY - bounds.top]
}
function pick(point: V2) {
  raycaster.setFromCamera(
    new THREE.Vector2((2 * point[0]) / width - 1, 1 - (2 * point[1]) / height),
    camera,
  )
  return raycaster.intersectObjects(
    [...meshes.values()].filter((m) => m.visible),
    false,
  )[0]
}
function down(event: PointerEvent) {
  if (event.button !== 0 || props.mode === 'orbit' || props.mode === 'edit') return
  const p = position(event)
  if (props.mode === 'draw' || props.mode === 'redraw') {
    drawing = true
    overlay.value!.setPointerCapture(event.pointerId)
    emit('begin', p)
  } else if (props.mode === 'skip') emit('skip', p)
  else if (props.mode === 'guide') {
    const intersection = pick(p)
    if (intersection) {
      const normal = intersection
        .face!.normal.clone()
        .transformDirection(intersection.object.matrixWorld)
      emit('guide', intersection.point.toArray() as V3, normal.toArray() as V3)
    } else emit('guide', [0, 0, 0], [0, 1, 0])
  }
}
function move(event: PointerEvent) {
  if (drawing) emit('move', position(event))
}
function up() {
  if (drawing) {
    drawing = false
    emit('finish')
  }
}
function objectPick(event: PointerEvent) {
  if (props.mode === 'edit' && !transform.dragging && !transform.axis) {
    const hit = pick(position(event))
    emit('pick', hit?.object.userData.id || '')
  }
}
onMounted(() => {
  try {
    scene = new THREE.Scene()
    scene.background = new THREE.Color('#f5f5f5')
    camera = new THREE.PerspectiveCamera(45, 1, 0.05, 300)
    renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setClearColor('#f5f5f5')
    host.value!.prepend(renderer.domElement)
    controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.addEventListener('change', () => emit('camera'))
    transform = new TransformControls(camera, renderer.domElement)
    scene.add(transform.getHelper())
    transform.addEventListener('mouseDown', () => emit('history'))
    transform.addEventListener('objectChange', () => {
      const mesh = transform.object as THREE.Mesh | undefined
      const shape = props.objects.find((o) => o.id === mesh?.userData.id)
      if (mesh && shape)
        emit('change', {
          ...shape,
          position: mesh.position.toArray() as V3,
          quaternion: mesh.quaternion.toArray() as [number, number, number, number],
          size: mesh.scale.toArray() as V3,
        })
    })
    const hemisphere = new THREE.HemisphereLight('#ffffff', '#999999', 2.6)
    scene.add(hemisphere)
    const key = new THREE.DirectionalLight('#ffffff', 3.5)
    key.position.set(4, 8, 6)
    scene.add(key)
    const grid = new THREE.GridHelper(24, 48, '#cfcfcf', '#e1e1e1')
    grid.position.y = -1.25
    scene.add(grid)
    scene.add(curveLayer, debugLayer)
    resize = new ResizeObserver(() => {
      width = Math.max(1, host.value!.clientWidth)
      height = Math.max(1, host.value!.clientHeight)
      renderer.setSize(width, height)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      const ratio = window.devicePixelRatio || 1
      overlay.value!.width = width * ratio
      overlay.value!.height = height * ratio
    })
    resize.observe(host.value!)
    renderer.domElement.addEventListener('pointerdown', objectPick)
    reset()
    updateObjects()
    updateCurves()
    const tick = () => {
      if (disposed) return
      frame = requestAnimationFrame(tick)
      controls.enabled = props.mode === 'orbit'
      if (controls.enabled) controls.update()
      renderer.render(scene, camera)
      paint()
    }
    tick()
  } catch (error) {
    failed.value = `三维视口无法初始化：${String(error)}`
  }
})
watch(() => [props.objects, props.selectedObject, props.mode], updateObjects, { deep: true })
watch(
  () => [
    props.curves,
    props.preview,
    props.selectedCurve,
    props.showRaw,
    props.showCandidates,
    props.xray,
  ],
  updateCurves,
  { deep: true },
)
watch(
  () => props.transformMode,
  (mode) => transform?.setMode(mode),
)
onBeforeUnmount(() => {
  disposed = true
  cancelAnimationFrame(frame)
  resize?.disconnect()
  controls?.dispose()
  transform?.dispose()
  clear(curveLayer)
  clear(debugLayer)
  meshes.forEach((mesh) => {
    mesh.geometry.dispose()
    ;(mesh.material as THREE.Material).dispose()
  })
  renderer?.dispose()
})
defineExpose({ snapshot, setCamera, reset, project })
</script>

<template>
  <div ref="host" class="viewport">
    <canvas
      ref="overlay"
      class="sketch-overlay"
      :class="{ capture: mode !== 'orbit' && mode !== 'edit' }"
      @pointerdown="down"
      @pointermove="move"
      @pointerup="up"
      @pointercancel="up"
      @lostpointercapture="up"
    />
    <div v-if="failed" class="viewport-failure">{{ failed }}</div>
  </div>
</template>
