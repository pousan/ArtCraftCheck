import bpy
import math
import os

BASE_DIR = "/Volumes/AdobeApps/ArtCraft"
BLEND_DIR = os.path.join(BASE_DIR, "outputs", "blend")
EXPORT_DIR = os.path.join(BASE_DIR, "outputs", "exports")
PREVIEW_DIR = os.path.join(BASE_DIR, "outputs", "renders", "previews")
FINAL_DIR = os.path.join(BASE_DIR, "outputs", "renders", "finals")


def clear_scene():
    """シーン内の全オブジェクト・データを初期化する"""
    bpy.ops.wm.read_factory_settings(use_empty=True)


def create_material(name, base_color=(0.8, 0.8, 0.8, 1.0), metallic=0.0, roughness=0.4):
    """Blender 5.2+ 対応の Principled BSDF マテリアルを作成して返す"""
    mat = bpy.data.materials.new(name=name)
    if mat.node_tree:
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = base_color
            bsdf.inputs["Roughness"].default_value = roughness
            if "Metallic" in bsdf.inputs:
                bsdf.inputs["Metallic"].default_value = metallic
    return mat


def apply_smooth_shading(obj):
    """オブジェクトの全ポリゴンにスムーズシェードを適用する"""
    if obj and obj.type == "MESH":
        for poly in obj.data.polygons:
            poly.use_smooth = True


def setup_studio(camera_loc=(0, -4.5, 1.2), camera_rot_deg=(75, 0, 0)):
    """カメラとスタジオライト（キーライト・フィルライト）を自動配置する"""
    scene = bpy.context.scene

    # カメラ配置
    camera_data = bpy.data.cameras.new(name="Camera")
    camera_obj = bpy.data.objects.new("Camera", camera_data)
    scene.collection.objects.link(camera_obj)
    scene.camera = camera_obj
    camera_obj.location = camera_loc
    camera_obj.rotation_euler = tuple(math.radians(a) for a in camera_rot_deg)

    # キーライト
    key_data = bpy.data.lights.new(name="KeyLight", type="AREA")
    key_data.energy = 400.0
    key_data.size = 2.0
    key_obj = bpy.data.objects.new("KeyLight", key_data)
    scene.collection.objects.link(key_obj)
    key_obj.location = (2.5, -3.0, 3.0)
    key_obj.rotation_euler = (math.radians(50), 0, math.radians(35))

    # フィルライト
    fill_data = bpy.data.lights.new(name="FillLight", type="AREA")
    fill_data.energy = 150.0
    fill_data.size = 3.0
    fill_obj = bpy.data.objects.new("FillLight", fill_data)
    scene.collection.objects.link(fill_obj)
    fill_obj.location = (-2.5, -3.0, 1.5)
    fill_obj.rotation_euler = (math.radians(65), 0, math.radians(-35))


def save_and_render(name, render_preview=True, export_glb=False, samples=32, resolution=(800, 600), is_final=False):
    """モデルを .blend 保存し、必要に応じて .glb エクスポートとプレビュー/本番レンダリングを行う"""
    scene = bpy.context.scene

    # 1. .blend 保存
    blend_path = os.path.join(BLEND_DIR, f"{name}.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    print(f"[SUCCESS] Saved blend file: {blend_path}")

    # 2. .glb エクスポート（オプション）
    if export_glb:
        glb_path = os.path.join(EXPORT_DIR, f"{name}.glb")
        bpy.ops.export_scene.gltf(filepath=glb_path, export_format="GLB")
        print(f"[SUCCESS] Exported GLB file: {glb_path}")

    # 3. レンダリング出力
    if render_preview:
        scene.render.engine = "CYCLES"
        if hasattr(scene, "cycles"):
            scene.cycles.samples = samples
            scene.cycles.use_denoising = True

        scene.render.resolution_x = resolution[0]
        scene.render.resolution_y = resolution[1]
        scene.render.image_settings.file_format = "PNG"

        out_dir = FINAL_DIR if is_final else PREVIEW_DIR
        suffix = "_final.png" if is_final else "_preview.png"
        render_path = os.path.join(out_dir, f"{name}{suffix}")
        scene.render.filepath = render_path

        print(f"[INFO] Rendering image ({resolution[0]}x{resolution[1]}, samples={samples})...")
        bpy.ops.render.render(write_still=True)
        print(f"[SUCCESS] Rendered image to: {render_path}")
