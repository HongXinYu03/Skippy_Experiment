export type V2 = [number, number]
export type V3 = [number, number, number]
export interface Shape {
  id: string
  kind: 'sphere' | 'box' | 'tetrahedron'
  position: V3
  quaternion: [number, number, number, number]
  size: V3
  enabled: boolean
  guide: boolean
  visible?: boolean
}
export interface CameraData { world: number[]; projection: number[]; width: number; height: number }
export interface Options { spacing: number; max_points: number; iterations: number; max_segments: number; depth_threshold: number; gradient_cosine: number; collision_tolerance: number; restore_pruned: boolean; closed: boolean | null; bridge_samples: number }
export interface SolveRequest { objects: Shape[]; camera: CameraData; points: V2[]; options: Options; locks: Record<number, string>; revision: number; request_id: string }
export interface Track { id: string; group: number; start: number; end: number; depth: number; selected: boolean; points: V3[] }
export interface Result {
  status: string; message?: string; request_id: string; revision: number; closed: boolean
  points: V3[]; raw_points: V3[]; control_points: V3[]; selected_tracks: string[]; display_kind: string
  metrics: Record<string, number | boolean>
  debug: { pixels: V2[]; on: boolean[]; heights: number[]; discontinuities: number[]; tracks: Track[]; candidates: {rank: number; depth: number; point: V3; object: string; pruned: boolean}[][]; smoothing: { energies: number[]; iterations: number }; graph: { nodes: {id: string; track: string; start: number; end: number; cost: number; selected: boolean}[]; edges: {from: string; to: string; cost: number}[] } }
}
export interface RedrawContext { original: V3[]; start: number; end: number }
export interface CurveRecord { id: string; name: string; result: Result; request: SolveRequest; sceneRevision: number; redraw?: RedrawContext }
export type Mode = 'draw' | 'orbit' | 'edit' | 'skip' | 'guide' | 'redraw'
