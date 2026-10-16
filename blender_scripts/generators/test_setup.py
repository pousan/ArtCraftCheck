import bpy
import sys
import os

# 共通モジュールのパスを追加
COMMON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common")
if COMMON_DIR not in sys.path:
    sys.path.append(COMMON_DIR)

import scene_utils


def main():
    # 1. シーン初期化
    scene_utils.clear_scene()

    # 2. テスト用メッシュ（スザンヌ）作成
    bpy.ops.mesh.primitive_monkey_add(size=2.0, location=(0, 0, 0))
    monkey = bpy.context.active_object
    monkey.name = "TestMonkey"

    # スムーズシェード＆サブディビジョンサーフェス適用
    scene_utils.apply_smooth_shading(monkey)
    subsurf = monkey.modifiers.new(name="Subsurf", type="SUBSURF")
    subsurf.levels = 2
    subsurf.render_levels = 2

    # マテリアル設定（DeprecationWarningなしの共通関数を使用）
    mat = scene_utils.create_material(
        name="MonkeyMaterial",
        base_color=(0.1, 0.6, 0.9, 1.0),
        metallic=0.6,
        roughness=0.25,
    )
    monkey.data.materials.append(mat)

    # 3. カメラとライトの配置
    scene_utils.setup_studio()

    # 4. 保存（.blend / .glb）とプレビュー画像レンダリング
    scene_utils.save_and_render(
        name="test_model",
        render_preview=True,
        export_glb=True,
        samples=32,
    )


if __name__ == "__main__":
    main()
