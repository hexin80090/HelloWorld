#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用 PyInstaller 构建 AppImage 的 Python 脚本
更精确的依赖控制和错误处理
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

APP_NAME = "QRViewer"
APP_VERSION = "1.0.0"
SCRIPT_DIR = Path(__file__).parent.absolute()
BUILD_DIR = SCRIPT_DIR / "build"
DIST_DIR = SCRIPT_DIR / "dist"
APP_DIR = BUILD_DIR / f"{APP_NAME}.AppDir"

def check_dependencies():
    """检查必需的依赖"""
    required_packages = [
        'PyInstaller',
        'cv2',
        'numpy',
        'PIL',
        'pynng',
        'turbojpeg',
        'dynamsoft_barcode_reader_bundle'
    ]
    
    missing = []
    for package in required_packages:
        try:
            if package == 'PIL':
                __import__('PIL')
            elif package == 'cv2':
                __import__('cv2')
            else:
                __import__(package.replace('-', '_'))
        except ImportError:
            missing.append(package)
    
    if missing:
        print(f"⚠️  缺少以下依赖: {', '.join(missing)}")
        print("请运行: pip3 install " + " ".join(missing))
        if 'PyInstaller' in missing:
            return False
    return True

def build_with_pyinstaller():
    """使用 PyInstaller 打包"""
    print("📦 使用 PyInstaller 打包应用...")
    
    # 准备 PyInstaller 参数
    main_script = SCRIPT_DIR / "qr_gui_viewer.py"
    if not main_script.exists():
        print(f"❌ 错误: 找不到主脚本 {main_script}")
        return False
    
    # 检查配置文件（可能在不同位置）
    config_file = SCRIPT_DIR / "camera_config.json"
    config_dir = SCRIPT_DIR / "config"
    
    # 尝试从不同位置找到配置文件
    if not config_file.exists():
        config_file = config_dir / "camera_config.json"
        if not config_file.exists():
            print("⚠️  警告: camera_config.json 不存在，将在打包目录中创建默认配置")
            config_file = SCRIPT_DIR / "camera_config.json"
            with open(config_file, 'w', encoding='utf-8') as f:
                f.write('''{
    "MaxParallelTasks": 8,
    "Timeout": 10000
}
''')
    
    # PyInstaller 命令
    cmd = [
        'pyinstaller',
        '--name', APP_NAME,
        '--onefile',
        '--windowed',  # 无控制台窗口
        f'--add-data={config_file}:config',
        '--hidden-import=tkinter',
        '--hidden-import=tkinter.ttk',
        '--hidden-import=tkinter.filedialog',
        '--hidden-import=cv2',
        '--hidden-import=numpy',
        '--hidden-import=PIL',
        '--hidden-import=PIL.Image',
        '--hidden-import=PIL.ImageTk',
        '--hidden-import=pynng',
        '--hidden-import=turbojpeg',
        '--hidden-import=dynamsoft_barcode_reader_bundle',
        '--collect-all=cv2',
        '--collect-all=numpy',
        '--collect-all=PIL',
        '--collect-all=pynng',
        '--collect-all=turbojpeg',
        '--collect-all=dynamsoft_barcode_reader_bundle',
        str(main_script)
    ]
    
    print(f"执行命令: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=SCRIPT_DIR)
    
    if result.returncode != 0:
        print("❌ PyInstaller 构建失败")
        return False
    
    executable = DIST_DIR / APP_NAME
    if not executable.exists():
        print(f"❌ 错误: 找不到生成的可执行文件 {executable}")
        return False
    
    print(f"✅ PyInstaller 构建成功: {executable}")
    return True

def create_appdir():
    """创建 AppDir 结构"""
    print("📁 创建 AppDir 结构...")
    
    # 清理旧的 AppDir
    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)
    
    # 创建目录结构
    (APP_DIR / "usr/bin").mkdir(parents=True)
    (APP_DIR / "usr/lib").mkdir(parents=True)
    (APP_DIR / "usr/share/applications").mkdir(parents=True)
    (APP_DIR / "usr/share/icons/hicolor/256x256/apps").mkdir(parents=True)
    
    # 复制可执行文件
    executable = DIST_DIR / APP_NAME
    if executable.exists():
        shutil.copy2(executable, APP_DIR / "usr/bin" / APP_NAME)
        os.chmod(APP_DIR / "usr/bin" / APP_NAME, 0o755)
    else:
        print(f"❌ 错误: 找不到可执行文件 {executable}")
        return False
    
    # 复制配置文件（尝试多个位置）
    config_file = SCRIPT_DIR / "camera_config.json"
    if not config_file.exists():
        config_file = SCRIPT_DIR / "config" / "camera_config.json"
    
    # 创建配置目录
    config_target_dir = APP_DIR / "usr/bin" / "config"
    config_target_dir.mkdir(parents=True, exist_ok=True)
    
    if config_file.exists():
        shutil.copy2(config_file, config_target_dir / "camera_config.json")
    else:
        # 创建默认配置
        print("⚠️  创建默认配置文件")
        with open(config_target_dir / "camera_config.json", 'w', encoding='utf-8') as f:
            f.write('''{
    "MaxParallelTasks": 8,
    "Timeout": 10000
}
''')
    
    # 创建 AppRun
    apprun_content = f'''#!/bin/bash
HERE="$(dirname "$(readlink -f "${{0}}")")"
export PATH="${{HERE}}/usr/bin:${{PATH}}"
export LD_LIBRARY_PATH="${{HERE}}/usr/lib:${{LD_LIBRARY_PATH}}"
exec "${{HERE}}/usr/bin/{APP_NAME}" "$@"
'''
    with open(APP_DIR / "AppRun", 'w') as f:
        f.write(apprun_content)
    os.chmod(APP_DIR / "AppRun", 0o755)
    
    # 创建 .desktop 文件
    desktop_content = f"""[Desktop Entry]
Name={APP_NAME}
Comment=二维码识别上位机界面程序
Exec={APP_NAME}
Icon={APP_NAME}
Type=Application
Categories=Utility;
"""
    desktop_file = APP_DIR / "usr/share/applications" / f"{APP_NAME}.desktop"
    with open(desktop_file, 'w', encoding='utf-8') as f:
        f.write(desktop_content)
    
    # 创建符号链接
    os.symlink("usr/share/applications/" + f"{APP_NAME}.desktop", 
               APP_DIR / f"{APP_NAME}.desktop")
    
    # 处理图标文件
    icon_source = SCRIPT_DIR / f"{APP_NAME}.png"
    icon_target = APP_DIR / "usr/share/icons/hicolor/256x256/apps" / f"{APP_NAME}.png"
    
    if icon_source.exists():
        # 如果项目根目录有图标文件，复制它
        shutil.copy2(icon_source, icon_target)
        print(f"✅ 使用图标: {icon_source}")
    else:
        # 创建简单的占位符图标
        print("📝 创建默认图标...")
        try:
            from PIL import Image
            img = Image.new('RGB', (256, 256), color=(70, 130, 180))  # 钢蓝色
            # 在图标上添加文字（如果可能）
            try:
                from PIL import ImageDraw, ImageFont
                draw = ImageDraw.Draw(img)
                # 尝试使用默认字体
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
                except:
                    font = ImageFont.load_default()
                text = "QR"
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                position = ((256 - text_width) // 2, (256 - text_height) // 2)
                draw.text(position, text, fill=(255, 255, 255), font=font)
            except:
                pass
            img.save(icon_target)
        except Exception as e:
            print(f"⚠️  创建图标失败: {e}，使用空图标占位符")
            icon_target.touch()
    
    # 创建符号链接
    if icon_target.exists():
        if (APP_DIR / f"{APP_NAME}.png").exists():
            (APP_DIR / f"{APP_NAME}.png").unlink()
        os.symlink("usr/share/icons/hicolor/256x256/apps/" + f"{APP_NAME}.png",
                   APP_DIR / f"{APP_NAME}.png")
    
    print(f"✅ AppDir 创建完成: {APP_DIR}")
    return True

def create_appimage():
    """使用 appimagetool 创建 AppImage"""
    print("🎨 创建 AppImage...")
    
    # 检查 appimagetool
    appimagetool = None
    appimagetool_path = BUILD_DIR / "appimagetool-x86_64.AppImage"
    
    if appimagetool_path.exists():
        appimagetool = str(appimagetool_path)
    elif shutil.which("appimagetool"):
        appimagetool = "appimagetool"
    else:
        print("📥 下载 appimagetool...")
        import urllib.request
        url = "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
        try:
            urllib.request.urlretrieve(url, appimagetool_path)
            os.chmod(appimagetool_path, 0o755)
            appimagetool = str(appimagetool_path)
        except Exception as e:
            print(f"❌ 下载 appimagetool 失败: {e}")
            print("请手动下载并安装 appimagetool")
            return False
    
    # 生成 AppImage
    output_file = DIST_DIR / f"{APP_NAME}-{APP_VERSION}-x86_64.AppImage"
    
    cmd = [appimagetool, str(APP_DIR), str(output_file)]
    print(f"执行命令: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    
    if result.returncode != 0:
        print("❌ AppImage 创建失败")
        return False
    
    if output_file.exists():
        size = output_file.stat().st_size / (1024 * 1024)
        print(f"✅ AppImage 创建成功: {output_file}")
        print(f"📦 文件大小: {size:.2f} MB")
        return True
    else:
        print(f"❌ 错误: AppImage 文件不存在 {output_file}")
        return False

def main():
    """主函数"""
    print(f"🚀 开始构建 {APP_NAME} AppImage...")
    
    # 清理旧的构建文件
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    if DIST_DIR.exists():
        # 只清理 PyInstaller 生成的文件，保留 AppImage
        for item in DIST_DIR.iterdir():
            if item.name != f"{APP_NAME}-{APP_VERSION}-x86_64.AppImage":
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
    
    BUILD_DIR.mkdir(exist_ok=True)
    DIST_DIR.mkdir(exist_ok=True)
    
    # 检查依赖
    if not check_dependencies():
        print("⚠️  依赖检查失败，但继续尝试构建...")
    
    # 构建步骤
    if not build_with_pyinstaller():
        print("❌ 构建失败")
        return 1
    
    if not create_appdir():
        print("❌ AppDir 创建失败")
        return 1
    
    if not create_appimage():
        print("❌ AppImage 创建失败")
        return 1
    
    print("\n🎉 构建完成！")
    print(f"📦 AppImage 位置: {DIST_DIR / f'{APP_NAME}-{APP_VERSION}-x86_64.AppImage'}")
    return 0

if __name__ == '__main__':
    sys.exit(main())

