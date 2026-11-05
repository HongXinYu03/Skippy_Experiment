"""Validated algorithm inputs; no HTTP or request-tracking fields."""
from typing import Literal
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)

class MeshData(Model):
    vertices: list[tuple[float, float, float]] = Field(min_length=3, max_length=100000)
    faces: list[tuple[int, int, int]] = Field(min_length=1, max_length=100000)

    @model_validator(mode="after")
    def validate_mesh(self):
        vertices = np.asarray(self.vertices)
        faces = np.asarray(self.faces)
        if faces.min() < 0 or faces.max() >= len(vertices):
            raise ValueError("Mesh face indices are out of range.")
        triangles = vertices[faces]
        extent = max(float(np.ptp(vertices, axis=0).max()), 1e-12)
        area = np.linalg.norm(np.cross(triangles[:, 1]-triangles[:, 0],
                                       triangles[:, 2]-triangles[:, 0]), axis=1)
        if np.any(area <= extent*extent*1e-14):
            raise ValueError("Mesh contains degenerate triangles.")
        return self


class Shape(Model):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["sphere", "box", "tetrahedron", "mesh"]
    position: tuple[float, float, float] = (0, 0, 0)
    quaternion: tuple[float, float, float, float] = (0, 0, 0, 1)
    size: tuple[float, float, float] = (1, 1, 1)
    enabled: bool = True
    guide: bool = False
    mesh: MeshData | None = None

    @model_validator(mode="after")
    def validate_geometry(self):
        if min(self.size) <= 0 or max(self.size) > 1000:
            raise ValueError("Object sizes must be in (0, 1000].")
        if np.linalg.norm(self.quaternion) < 1e-10:
            raise ValueError("Quaternion must be nonzero.")
        if (self.kind == "mesh") != (self.mesh is not None):
            raise ValueError("Only mesh objects must include mesh geometry.")
        return self

class Camera(Model):
    world: list[float] = Field(min_length=16, max_length=16)
    projection: list[float] = Field(min_length=16, max_length=16)
    width: float = Field(gt=0, le=16384)
    height: float = Field(gt=0, le=16384)

    @model_validator(mode="after")
    def invertible(self):
        for matrix in (self.world, self.projection):
            if abs(np.linalg.det(np.asarray(matrix).reshape(4, 4, order="F"))) < 1e-12:
                raise ValueError("Camera matrices must be invertible.")
        if abs(self.projection[15]) > 1e-8:
            raise ValueError("This implementation expects a perspective camera.")
        return self

class Options(Model):
    spacing: float = Field(default=8, ge=2, le=30)
    max_points: int = Field(default=240, ge=16, le=400)
    iterations: int = Field(default=80, ge=0, le=512)
    max_segments: int = Field(default=20000, ge=20, le=30000)
    depth_threshold: float = Field(default=0.25, gt=0, le=2)
    gradient_cosine: float = Field(default=-0.5, ge=-1, le=1)
    collision_tolerance: float = Field(default=0.0005, ge=0.000001, le=0.01)
    restore_pruned: bool = False
    bridge_samples: int = Field(default=16, ge=2, le=64)
    closed: bool | None = None

class SolveInput(Model):
    """Input for the solve operation."""
    objects: list[Shape] = Field(max_length=32)                 # 场景中的对象列表，最多包含32个对象
    camera: Camera                                              # 相机参数，包括世界矩阵、投影矩阵、宽度和高度
    points: list[tuple[float, float]] = Field(min_length=2, max_length=12000)   # 点集，表示笔触的二维坐标，至少包含2个点，最多包含12000个点
    options: Options = Field(default_factory=Options)           # 求解选项，包括间距、最大点数、迭代次数等
    locks: dict[int, str] = Field(default_factory=dict)         # 锁定点的字典，键为点的索引，值为锁定的类型（例如"fixed"表示固定点）

class RedrawInput(Model):
    solve: SolveInput
    original: list[tuple[float, float, float]] = Field(min_length=2, max_length=6000)
    start: int = Field(ge=0)
    end: int = Field(ge=1)

class SolveError(Exception):
    def __init__(self, status: str, message: str):
        self.status, self.message = status, message
        super().__init__(message)
