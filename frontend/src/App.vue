<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import Viewport from './components/Viewport.vue'
import type {
  CameraData,
  CurveRecord,
  Mode,
  Options,
  Result,
  Shape,
  SolveRequest,
  V2,
  V3,
  RedrawContext,
} from './types'

const viewport = ref<InstanceType<typeof Viewport>>()
const objects = ref<Shape[]>([
  {
    id: 'sphere-1',
    kind: 'sphere',
    position: [0, 0, 0],
    quaternion: [0, 0, 0, 1],
    size: [1, 1, 1],
    enabled: true,
    guide: false,
  },
])
const curves = ref<CurveRecord[]>([])
const selectedObject = ref('sphere-1'),
  selectedCurve = ref('')
const mode = ref<Mode>('draw'),
  transformMode = ref<'translate' | 'rotate' | 'scale'>('translate')
const stroke = ref<V2[]>([]),
  preview = ref<Result | null>(null)
const showRaw = ref(false),
  showCandidates = ref(false),
  samples = ref(false),
  xray = ref(false),
  live = ref(true)
const options = ref<Options>({
  spacing: 8,
  max_points: 240,
  iterations: 80,
  max_segments: 4800,
  depth_threshold: 0.25,
  gradient_cosine: -0.5,
  collision_tolerance: 0.0005,
  restore_pruned: false,
  closed: null,
  bridge_samples: 16,
})
const apiBase = ref(import.meta.env.VITE_API_BASE_URL || '')
const connected = ref(false),
  busy = ref(false),
  message = ref('就绪')
const tab = ref<'depth' | 'debug' | 'graph'>('depth')
const exampleKind = ref('sphere'),
  sceneRevision = ref(0)
const rangeStart = ref(20),
  rangeEnd = ref(80),
  groupSelection = ref(0)
const selectedShape = computed(() => objects.value.find((o) => o.id === selectedObject.value))
const current = computed(() => curves.value.find((c) => c.id === selectedCurve.value))
const currentResult = computed(() => preview.value || current.value?.result)
const groups = computed(() =>
  [...new Set((current.value?.result.debug.tracks || []).map((t) => t.group))].sort(
    (a, b) => a - b,
  ),
)
const tracks = computed(() =>
  (current.value?.result.debug.tracks || [])
    .filter((t) => t.group === groupSelection.value)
    .sort((a, b) => a.depth - b.depth),
)
const history = ref<string[]>([]),
  future = ref<string[]>([])
let sourceCamera: CameraData | null = null,
  lastPreview = 0,
  sequence = 0,
  drawing = false
let controller: AbortController | undefined
const kindName = { sphere: '球体', box: '长方体', tetrahedron: '四面体' }
const modeHints: Record<Mode, string> = {
  draw: '在画布上绘制，松开生成曲线',
  orbit: '拖动旋转，右键平移，滚轮缩放',
  edit: '选择物体，使用坐标轴调整位置、旋转或缩放',
  skip: '点击曲线附近切换深度候选',
  guide: '点击表面添加引导球，空白处放在原点',
  redraw: '设置区间后，沿原曲线附近重画',
}
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const identifier = () => crypto.randomUUID()
const format = (value: number | boolean | undefined, digits = 2) =>
  typeof value === 'number' ? value.toFixed(digits) : String(value ?? '—')

function selectObject(id: string) {
  selectedObject.value = id
  mode.value = 'edit'
}
function selectCurve(id: string) {
  selectedCurve.value = id
  preview.value = null
}
function state() {
  return JSON.stringify({
    objects: objects.value,
    curves: curves.value,
    selectedObject: selectedObject.value,
    selectedCurve: selectedCurve.value,
  })
}
function remember() {
  history.value.push(state())
  if (history.value.length > 24) history.value.shift()
  future.value = []
}
function restore(serialized: string) {
  const value = JSON.parse(serialized)
  objects.value = value.objects
  curves.value = value.curves
  selectedObject.value = value.selectedObject
  selectedCurve.value = value.selectedCurve
  sceneRevision.value++
  sequence++
  controller?.abort()
  preview.value = null
  stroke.value = []
}
function undo() {
  const value = history.value.pop()
  if (value) {
    future.value.push(state())
    restore(value)
  }
}
function redo() {
  const value = future.value.pop()
  if (value) {
    history.value.push(state())
    restore(value)
  }
}
function add(kind: Shape['kind'], guide = false, position: V3 = [0, 0, 0], normal: V3 = [0, 1, 0]) {
  remember()
  const quaternion = new THREE.Quaternion().setFromUnitVectors(
    new THREE.Vector3(0, 1, 0),
    new THREE.Vector3(...normal),
  )
  const id = identifier()
  objects.value.push({
    id,
    kind,
    position,
    quaternion: quaternion.toArray() as Shape['quaternion'],
    size: guide ? [0.65, 0.65, 0.65] : kind === 'box' ? [1.4, 1.4, 1.4] : [1, 1, 1],
    enabled: true,
    guide,
  })
  selectedObject.value = id
  sceneRevision.value++
  mode.value = 'edit'
}
function removeObject() {
  if (!selectedShape.value) return
  remember()
  objects.value = objects.value.filter((o) => o.id !== selectedObject.value)
  selectedObject.value = ''
  sceneRevision.value++
}
function updateShape(shape: Shape) {
  const index = objects.value.findIndex((o) => o.id === shape.id)
  if (index >= 0) {
    shape.size = shape.size.map((v) => Math.max(0.03, v)) as V3
    objects.value[index] = shape
    sceneRevision.value++
  }
}
function commitShape() {
  sceneRevision.value++
}
function shapes(): Shape[] {
  return objects.value.map(({ id, kind, position, quaternion, size, enabled, guide }) => ({
    id,
    kind,
    position,
    quaternion,
    size,
    enabled,
    guide,
  }))
}
function request(points: V2[], camera = sourceCamera): SolveRequest {
  if (!camera) throw new Error('创作相机未就绪。')
  return {
    objects: clone(shapes()),
    camera: clone(camera),
    points: clone(points),
    options: clone(options.value),
    locks: {},
    revision: sceneRevision.value,
    request_id: identifier(),
  }
}
async function health() {
  try {
    connected.value = (await fetch(`${apiBase.value}/api/health`)).ok
  } catch {
    connected.value = false
  }
}
async function execute(
  input: SolveRequest,
  final: boolean,
  curveId?: string,
  redrawContext?: RedrawContext,
) {
  const serial = ++sequence
  controller?.abort()
  controller = new AbortController()
  busy.value = true
  try {
    let body: unknown = input
    const existingCurve = curves.value.find((c) => c.id === curveId)
    const redraw = redrawContext || existingCurve?.redraw
    if (redraw) body = { solve: input, ...redraw }
    const response = await fetch(`${apiBase.value}/api/${redraw ? 'redraw' : 'solve'}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
    if (!response.ok) throw new Error(`请求失败（${response.status}），请检查输入或后端日志。`)
    const result = (await response.json()) as Result
    if (serial !== sequence || input.revision !== sceneRevision.value) return
    connected.value = true
    if (result.status !== 'ok') {
      if (final) message.value = result.message || result.status
      return
    }
    if (!final) {
      preview.value = result
      return
    }
    const existing = curveId && curves.value.find((c) => c.id === curveId)
    if (existing) {
      existing.result = result
      existing.request = input
      existing.sceneRevision = sceneRevision.value
      existing.redraw = redraw
    } else {
      const id = identifier()
      curves.value.push({
        id,
        name: `曲线 ${curves.value.length + 1}`,
        result,
        request: input,
        sceneRevision: sceneRevision.value,
      })
      selectedCurve.value = id
    }
    message.value = `已生成 · ${format(result.metrics.total_ms, 0)} ms`
    preview.value = null
    stroke.value = []
    tab.value = 'depth'
    groupSelection.value = groups.value[0] ?? 0
  } catch (error) {
    if ((error as Error).name !== 'AbortError' && final) {
      connected.value = false
      message.value = `${String(error)} 后端启动命令见项目 README。`
    }
  } finally {
    if (serial === sequence) busy.value = false
  }
}
function begin(point: V2) {
  if (mode.value === 'redraw' && !current.value) {
    message.value = '先选择一条曲线，再设置重画区间。'
    return
  }
  remember()
  sourceCamera = viewport.value!.snapshot()
  stroke.value = [point]
  preview.value = null
  drawing = true
}
function move(point: V2) {
  if (!drawing) return
  const previous = stroke.value[stroke.value.length - 1]!
  if (Math.hypot(point[0] - previous[0], point[1] - previous[1]) < 2) return
  stroke.value.push(point)
  if (
    live.value &&
    mode.value === 'draw' &&
    stroke.value.length > 5 &&
    !busy.value &&
    performance.now() - lastPreview > 220
  ) {
    lastPreview = performance.now()
    const input = request(stroke.value)
    input.options.iterations = 8
    void execute(input, false)
  }
}
function finish() {
  if (!drawing) return
  drawing = false
  if (stroke.value.length < 3) {
    stroke.value = []
    return
  }
  const n = current.value?.result.points.length || 0
  const redraw =
    mode.value === 'redraw' && current.value
      ? {
          original: clone(current.value.result.points),
          start: Math.floor((rangeStart.value / 100) * (n - 1)),
          end: Math.ceil((rangeEnd.value / 100) * (n - 1)),
        }
      : undefined
  void execute(request(stroke.value), true, redraw ? selectedCurve.value : undefined, redraw)
}
async function chooseTrack(track: string, group = groupSelection.value) {
  if (!current.value) return
  if (current.value.sceneRevision !== sceneRevision.value) {
    message.value = '场景已变化，请先基于当前场景重新求解。'
    return
  }
  remember()
  const input = clone(current.value.request)
  input.objects = shapes()
  input.options = clone(current.value.request.options)
  input.options.iterations = options.value.iterations
  input.revision = sceneRevision.value
  input.request_id = identifier()
  if (track) input.locks[group] = track
  else delete input.locks[group]
  await execute(input, true, current.value.id)
}
function skipAt(point: V2) {
  if (!current.value) return
  const candidates = current.value.result.debug.tracks.filter((t) => t.selected)
  let nearest = candidates[0],
    distance = Infinity
  for (const track of candidates)
    for (const p of track.points) {
      const q = viewport.value!.project(p),
        d = Math.hypot(q[0] - point[0], q[1] - point[1])
      if (d < distance) {
        distance = d
        nearest = track
      }
    }
  if (!nearest || distance > 75) {
    message.value = '点击曲线的 on 段附近，或在右侧选择候选。'
    return
  }
  groupSelection.value = nearest.group
  const choices = current.value.result.debug.tracks
    .filter((t) => t.group === nearest.group)
    .sort((a, b) => a.depth - b.depth)
  const index = choices.findIndex((t) => t.selected)
  const next = choices[(index + 1) % choices.length]
  if (next) void chooseTrack(next.id, nearest.group)
}
function recompute() {
  if (!current.value) return
  const input = clone(current.value.request)
  input.objects = shapes()
  input.options = clone(options.value)
  input.locks = {}
  input.revision = sceneRevision.value
  input.request_id = identifier()
  remember()
  void execute(input, true, current.value.id)
}
async function demo() {
  try {
    const size = viewport.value!.snapshot()
    const response = await fetch(
      `${apiBase.value}/api/examples/${exampleKind.value}?width=${Math.round(size.width)}&height=${Math.round(size.height)}`,
    )
    if (!response.ok) throw new Error('示例加载失败。')
    const input = (await response.json()) as SolveRequest
    remember()
    objects.value = input.objects.map((o) => ({ ...o, visible: true }))
    curves.value = []
    selectedCurve.value = ''
    selectedObject.value = ''
    sceneRevision.value++
    await nextTick()
    viewport.value!.setCamera(input.camera)
    input.revision = sceneRevision.value
    input.request_id = identifier()
    input.options.iterations = options.value.iterations
    stroke.value = input.points
    await execute(input, true)
  } catch (error) {
    message.value = `${String(error)} 请先启动 Python 后端。`
  }
}
function download(name: string, value: unknown) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }),
  )
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  URL.revokeObjectURL(url)
}
function save() {
  download('skippy-scene.json', {
    schema: 'skippy-studio/1',
    objects: objects.value,
    curves: curves.value,
    camera: viewport.value!.snapshot(),
  })
}
async function load(event: Event) {
  const element = event.target as HTMLInputElement,
    file = element.files?.[0]
  if (!file) return
  try {
    if (file.size > 12_000_000) throw new Error('文件超过 12 MB。')
    const value = JSON.parse(await file.text())
    if (
      value.schema !== 'skippy-studio/1' ||
      !Array.isArray(value.objects) ||
      value.objects.length > 32 ||
      !Array.isArray(value.curves)
    )
      throw new Error('场景文件格式无效。')
    if (
      value.objects.some(
        (o: Shape) =>
          !['sphere', 'box', 'tetrahedron'].includes(o.kind) ||
          !Array.isArray(o.position) ||
          o.position.length !== 3 ||
          !Array.isArray(o.size) ||
          o.size.length !== 3 ||
          !Array.isArray(o.quaternion) ||
          o.quaternion.length !== 4,
      )
    )
      throw new Error('几何体参数无效。')
    remember()
    objects.value = value.objects
    curves.value = value.curves
    selectedObject.value = ''
    selectedCurve.value = value.curves[0]?.id || ''
    sceneRevision.value++
    await nextTick()
    if (value.camera) viewport.value!.setCamera(value.camera)
    message.value = '场景已载入'
  } catch (error) {
    message.value = String(error)
  }
  element.value = ''
}
function deleteCurve() {
  if (current.value) {
    remember()
    curves.value = curves.value.filter((c) => c.id !== selectedCurve.value)
    selectedCurve.value = curves.value.at(-1)?.id || ''
  }
}
const graph = computed(() => {
  const source = currentResult.value?.debug.graph
  if (!source) return { nodes: [], edges: [] }
  const visible = source.nodes.filter((n, i) => i < 80 || n.selected)
  const maximum = Math.max(1, ...visible.map((n) => n.end))
  const nodes = visible.map((n, i) => ({
    ...n,
    x: 15 + ((n.start + n.end) / 2 / maximum) * 280,
    y: 15 + (i / Math.max(1, visible.length - 1)) * 165,
  }))
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const edges = source.edges
    .filter((e) => byId.has(e.from) && byId.has(e.to))
    .map((e) => ({ ...e, a: byId.get(e.from)!, b: byId.get(e.to)! }))
  return { nodes, edges }
})
const heightPlot = computed(() => {
  const h = currentResult.value?.debug.heights || [],
    max = Math.max(...h, 0.001)
  return h
    .map((v, i) => `${10 + (i / Math.max(1, h.length - 1)) * 280},${90 - (v / max) * 72}`)
    .join(' ')
})
watch(apiBase, () => void health())
watch(mode, () => {
  if (!drawing) stroke.value = []
})
onMounted(() => void health())
</script>

<template>
  <div class="studio">
    <header class="topbar">
      <strong class="brand">Skippy <span>Studio</span></strong>
      <div class="top-actions">
        <span
          class="connection"
          :class="{ online: connected }"
          :title="connected ? '后端服务已连接' : '请启动后端服务'"
          >{{ connected ? '已连接' : '未连接' }}</span
        >
        <label class="button"
          >导入<input type="file" accept="application/json" hidden @change="load"
        /></label>
        <button class="primary" @click="save">保存</button>
      </div>
    </header>
    <aside class="left-panel">
      <div class="panel-heading">
        场景 <span class="count">{{ objects.length }}</span>
      </div>
      <div class="add-shapes">
        <button @click="add('sphere')"><span class="shape-icon sphere" />球体</button
        ><button @click="add('box')"><span class="shape-icon box" />长方体</button
        ><button @click="add('tetrahedron')"><span class="shape-icon tetra" />四面体</button>
      </div>
      <div class="object-list">
        <button
          v-for="(object, i) in objects"
          :key="object.id"
          :class="{ selected: selectedObject === object.id }"
          @click="selectObject(object.id)"
        >
          <span class="object-dot" :class="{ guide: object.guide }" /><span
            >{{ object.guide ? '引导' : '' }}{{ kindName[object.kind] }} {{ i + 1 }}</span
          ><small v-if="!object.enabled">已忽略</small>
        </button>
        <p v-if="!objects.length" class="quiet">暂无物体</p>
      </div>
      <template v-if="selectedShape">
        <div class="section-label">属性</div>
        <label class="field"
          >位置
          <span class="vector-fields"
            ><input
              v-for="(_, i) in selectedShape.position"
              :key="i"
              v-model.number="selectedShape.position[i]"
              type="number"
              step="0.1"
              :aria-label="`位置 ${i}`"
              @focus="remember"
              @change="commitShape" /></span
        ></label>
        <label class="field"
          >{{ selectedShape.kind === 'box' ? '尺寸' : '半径' }}
          <span class="vector-fields"
            ><input
              v-model.number="selectedShape.size[0]"
              type="number"
              step="0.1"
              min="0.03"
              aria-label="尺寸 X 或半径"
              @focus="remember"
              @change="commitShape" /><template v-if="selectedShape.kind === 'box'"
              ><input
                v-model.number="selectedShape.size[1]"
                type="number"
                step="0.1"
                min="0.03"
                aria-label="尺寸 Y"
                @focus="remember"
                @change="commitShape" /><input
                v-model.number="selectedShape.size[2]"
                type="number"
                step="0.1"
                min="0.03"
                aria-label="尺寸 Z"
                @focus="remember"
                @change="commitShape" /></template></span
        ></label>
        <div class="property-toggles">
          <label class="check" title="将该物体用于曲线推断"
            ><input
              v-model="selectedShape.enabled"
              type="checkbox"
              @change="commitShape"
            />参与推断</label
          ><label class="check"
            ><input
              :checked="selectedShape.visible !== false"
              type="checkbox"
              @change="selectedShape.visible = ($event.target as HTMLInputElement).checked"
            />显示</label
          >
        </div>
        <div class="transform-buttons">
          <button
            v-for="item in [
              ['translate', '移动'],
              ['rotate', '旋转'],
              ['scale', '缩放'],
            ] as const"
            :key="item[0]"
            :class="{ active: transformMode === item[0] }"
            @click="transformMode = item[0]"
          >
            {{ item[1] }}
          </button>
        </div>
        <button class="text-action" @click="removeObject">删除物体</button>
      </template>
      <div class="section-label section-spaced">
        曲线 <span>{{ curves.length }}</span>
      </div>
      <div class="curve-list">
        <button
          v-for="curve in curves"
          :key="curve.id"
          :class="{ selected: selectedCurve === curve.id }"
          @click="selectCurve(curve.id)"
        >
          <span class="curve-symbol">∿</span>{{ curve.name
          }}<small v-if="curve.result.closed">闭合</small>
        </button>
        <p v-if="!curves.length" class="quiet">暂无曲线</p>
      </div>
      <div v-if="current" class="row-actions">
        <button @click="download('skippy-input.json', current.request)">导出输入</button
        ><button @click="download('skippy-result.json', current.result)">导出诊断</button
        ><button class="text-action" @click="deleteCurve">删除</button>
      </div>
    </aside>
    <main class="canvas-panel">
      <div class="canvas-toolbar">
        <div class="mode-buttons">
          <button
            v-for="item in [
              ['draw', '绘制'],
              ['orbit', '观察'],
              ['edit', '编辑'],
              ['skip', '深度'],
              ['guide', '引导'],
              ['redraw', '重画'],
            ] as const"
            :key="item[0]"
            :class="{ active: mode === item[0] }"
            :title="modeHints[item[0]]"
            @click="mode = item[0]"
          >
            {{ item[1] }}
          </button>
        </div>
        <div class="view-actions">
          <button :disabled="!history.length" title="撤销" aria-label="撤销" @click="undo">↶</button
          ><button :disabled="!future.length" title="重做" aria-label="重做" @click="redo">↷</button
          ><button title="恢复观察视角" @click="viewport?.reset()">复位</button>
        </div>
      </div>
      <Viewport
        ref="viewport"
        :objects="objects"
        :curves="curves"
        :selected-object="selectedObject"
        :selected-curve="selectedCurve"
        :mode="mode"
        :preview="preview"
        :stroke="stroke"
        :show-raw="showRaw"
        :show-candidates="showCandidates"
        :xray="xray"
        :samples="samples"
        :transform-mode="transformMode"
        @begin="begin"
        @move="move"
        @finish="finish"
        @pick="selectedObject = $event"
        @skip="skipAt"
        @guide="(p, n) => add('sphere', true, p, n)"
        @change="updateShape"
        @history="remember"
      />
      <div class="example-strip">
        <span>示例</span
        ><select v-model="exampleKind" aria-label="选择示例">
          <option value="sphere">球体</option>
          <option value="box">长方体</option>
          <option value="tetrahedron">四面体</option>
          <option value="two-spheres">双球体</option>
          <option value="loop">闭合曲线</option>
          <option value="discontinuity">深度桥接</option></select
        ><button :disabled="busy" title="载入示例并求解" @click="demo">运行</button
        ><label class="check" title="绘制过程中预览计算结果"
          ><input v-model="live" type="checkbox" />预览</label
        >
      </div>
    </main>
    <aside class="right-panel">
      <div class="panel-heading">
        曲线 <span class="count">{{ busy ? '计算中' : current ? '已生成' : '待绘制' }}</span>
      </div>
      <div v-if="currentResult" class="tabs">
        <button
          v-for="item in [
            ['depth', '深度'],
            ['debug', '诊断'],
            ['graph', '段图'],
          ] as const"
          :key="item[0]"
          :class="{ active: tab === item[0] }"
          @click="tab = item[0]"
        >
          {{ item[1] }}
        </button>
      </div>
      <div v-if="currentResult && tab === 'debug'" class="metric-summary">
        <div>
          <strong>{{ format(currentResult.metrics.projection_p95_px) }}<small>px</small></strong
          ><span>投影 P95</span>
        </div>
        <div>
          <strong>{{ format(currentResult.metrics.total_ms, 0) }}<small>ms</small></strong
          ><span>耗时</span>
        </div>
      </div>
      <template v-if="tab === 'depth'">
        <div v-if="current" class="depth-controls">
          <label class="field"
            >区段<select v-model.number="groupSelection" aria-label="曲线区段">
              <option v-for="group in groups" :key="group" :value="group">{{ group + 1 }}</option>
            </select></label
          ><button
            v-for="(track, i) in tracks"
            :key="track.id"
            class="track-option"
            :class="{ selected: track.selected }"
            :disabled="busy"
            @click="chooseTrack(track.id)"
          >
            <span class="radio-dot" />
            <div>
              <strong>候选 {{ i + 1 }}</strong
              ><small>距相机 {{ track.depth.toFixed(3) }}</small>
            </div>
            <span v-if="track.selected">✓</span>
          </button>
          <div class="depth-actions">
            <button :disabled="busy" @click="chooseTrack('')">自动选择</button
            ><button :disabled="busy" @click="recompute">重新求解</button>
          </div>
          <p v-if="current.sceneRevision !== sceneRevision" class="notice">
            场景已更新，请重新求解。
          </p>
        </div>
        <div v-else class="intro-note"><p>在画布上绘制，松开生成曲线。</p></div>
        <div v-if="mode === 'redraw' && current" class="redraw-controls">
          <div class="section-label">重画区间</div>
          <label class="field"
            >起点 {{ rangeStart }}%<input
              v-model.number="rangeStart"
              type="range"
              min="0"
              :max="rangeEnd - 5" /></label
          ><label class="field"
            >终点 {{ rangeEnd }}%<input
              v-model.number="rangeEnd"
              type="range"
              :min="rangeStart + 5"
              max="100"
          /></label>
        </div>
      </template>
      <template v-if="tab === 'debug' && currentResult">
        <div class="diagnostic-list">
          <div>
            <span>采样点</span><strong>{{ currentResult.metrics.sample_count }}</strong>
          </div>
          <div>
            <span>节点 / 边</span
            ><strong>{{ currentResult.metrics.nodes }} / {{ currentResult.metrics.edges }}</strong>
          </div>
          <div title="归一化离物高度误差的第 95 百分位">
            <span>高度 P95</span
            ><strong>{{ format(currentResult.metrics.height_p95_normalized, 5) }}</strong>
          </div>
          <div>
            <span>最小离物距离</span
            ><strong>{{ format(currentResult.metrics.minimum_clearance_world, 5) }}</strong>
          </div>
          <div>
            <span>平滑迭代</span><strong>{{ currentResult.debug.smoothing.iterations }}</strong>
          </div>
          <div>
            <span>缓存</span
            ><strong>{{ currentResult.metrics.cache_hit ? '命中' : '新建' }}</strong>
          </div>
          <div>
            <span>曲线类型</span
            ><strong>{{
              currentResult.display_kind === 'catmull_rom' ? 'Catmull–Rom' : '折线'
            }}</strong>
          </div>
        </div>
        <div class="mini-chart">
          <div class="section-label">离物高度</div>
          <svg viewBox="0 0 300 105" role="img" aria-label="高度随笔触进度变化">
            <path d="M10 10 V90 H290" fill="none" stroke="#dedede" />
            <polyline :points="heightPlot" fill="none" stroke="#656565" stroke-width="2" />
            <text x="10" y="103">起点</text>
            <text x="265" y="103">终点</text>
          </svg>
        </div>
        <button class="wide-button" @click="download('skippy-diagnostics.json', currentResult)">
          导出诊断
        </button>
      </template>
      <template v-if="tab === 'graph' && currentResult"
        ><div
          class="mini-chart"
          title="横向为笔触进度，纵向为候选段；显示前 80 个节点及全部选中节点。"
        >
          <svg viewBox="0 0 310 200" role="img" aria-label="段图：横轴为笔触进度，纵向为候选段">
            <line
              v-for="edge in graph.edges"
              :key="edge.from + edge.to"
              :x1="edge.a.x"
              :y1="edge.a.y"
              :x2="edge.b.x"
              :y2="edge.b.y"
              stroke="#dedede"
            />
            <circle
              v-for="node in graph.nodes"
              :key="node.id"
              :cx="node.x"
              :cy="node.y"
              :r="node.selected ? 4 : 2.5"
              :fill="node.selected ? '#333333' : '#bcbcbc'"
            >
              <title>{{ node.id }} · cost {{ node.cost.toFixed(3) }}</title>
            </circle>
          </svg>
        </div></template
      >
      <details class="display-settings">
        <summary>显示</summary>
        <div class="display-controls">
          <label class="check"><input v-model="samples" type="checkbox" />采样点</label
          ><label class="check"><input v-model="showRaw" type="checkbox" />原始曲线</label
          ><label class="check"><input v-model="showCandidates" type="checkbox" />候选顶点</label
          ><label class="check"><input v-model="xray" type="checkbox" />透视遮挡</label>
        </div>
      </details>
      <details class="solver-settings">
        <summary>参数</summary>
        <label class="field"
          >采样间隔（px）<input
            v-model.number="options.spacing"
            type="number"
            min="2"
            max="30" /></label
        ><label class="field"
          >迭代上限<input
            v-model.number="options.iterations"
            type="number"
            min="0"
            max="512" /></label
        ><label class="check"
          ><input v-model="options.restore_pruned" type="checkbox" />恢复剪枝候选</label
        ><label class="field"
          >后端地址<input
            v-model="apiBase"
            placeholder="本地代理"
            aria-label="Python API 地址" /></label
        ><button class="wide-button" @click="health">检查连接</button>
      </details>
    </aside>
    <footer class="statusbar">
      <span class="status-dot" :class="{ working: busy }" /><span>{{
        busy ? '计算中…' : message
      }}</span>
    </footer>
  </div>
</template>
