#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用 PyInstaller 构建 Windows 64位 exe 的 Python 脚本
需要在 Windows 环境下运行
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

def check_dependencies(auto_install=False):
    """检查必需的依赖，可选自动安装"""
    # 包名映射：import名称 -> pip安装名称
    package_map = {
        'PyInstaller': 'PyInstaller',
        'cv2': 'opencv-python',
        'numpy': 'numpy',
        'PIL': 'Pillow',
        'pynng': 'pynng',
        'turbojpeg': 'turbojpeg',
        'dynamsoft_barcode_reader_bundle': 'dynamsoft-barcode-reader-bundle'
    }
    
    missing = []
    for import_name, pip_name in package_map.items():
        try:
            if import_name == 'PIL':
                __import__('PIL')
            elif import_name == 'cv2':
                __import__('cv2')
            else:
                __import__(import_name.replace('-', '_'))
        except ImportError:
            missing.append(pip_name)
    
    if missing:
        print(f"⚠️  缺少以下依赖: {', '.join(missing)}")
        
        if auto_install:
            print("🔧 正在自动安装缺失的依赖...")
            try:
                import subprocess
                result = subprocess.run(
                    [sys.executable, '-m', 'pip', 'install'] + missing,
                    check=True,
                    capture_output=True,
                    text=True
                )
                print("✅ 依赖安装成功")
                # 重新检查
                return check_dependencies(auto_install=False)
            except subprocess.CalledProcessError as e:
                print(f"❌ 自动安装失败: {e}")
                print(f"请手动运行: pip install {' '.join(missing)}")
                return False
        else:
            print("💡 提示: 运行脚本时添加 --install-deps 参数可自动安装依赖")
            print(f"   或手动运行: pip install {' '.join(missing)}")
            # 检查是否有requirements文件
            req_file = SCRIPT_DIR / "requirements_build.txt"
            if req_file.exists():
                print(f"   或使用: pip install -r requirements_build.txt")
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
    
    # PyInstaller 命令（Windows 使用分号作为路径分隔符）
    cmd = [
        'pyinstaller',
        '--name', APP_NAME,
        '--onefile',
        '--windowed',  # 无控制台窗口
        f'--add-data={config_file}{os.pathsep}config',  # Windows 使用分号
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
    
    # 如果存在图标文件，添加图标参数
    icon_file = SCRIPT_DIR / f"{APP_NAME}.ico"
    if icon_file.exists():
        cmd.insert(-1, '--icon')
        cmd.insert(-1, str(icon_file))
    
    print(f"执行命令: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=SCRIPT_DIR)
    
    if result.returncode != 0:
        print("❌ PyInstaller 构建失败")
        return False
    
    executable = DIST_DIR / f"{APP_NAME}.exe"
    if not executable.exists():
        print(f"❌ 错误: 找不到生成的可执行文件 {executable}")
        return False
    
    print(f"✅ PyInstaller 构建成功: {executable}")
    
    # 复制配置文件到 dist 目录（可选，因为已经打包到 exe 中）
    config_target_dir = DIST_DIR / "config"
    config_target_dir.mkdir(exist_ok=True)
    if config_file.exists():
        shutil.copy2(config_file, config_target_dir / "camera_config.json")
        print(f"✅ 配置文件已复制到: {config_target_dir}")
    
    # 创建启动器批处理文件（自动添加--dbr选项）
    launcher_bat = DIST_DIR / f"{APP_NAME}_启动.bat"
    bat_content = f'''@echo off
REM {APP_NAME} 启动器 - 自动启用DBR识别
cd /d "%~dp0"
start "" "{APP_NAME}.exe" --dbr %*
'''
    with open(launcher_bat, 'w', encoding='gbk') as f:
        f.write(bat_content)
    print(f"✅ 启动器批处理文件已创建: {launcher_bat}")
    
    return True

def create_installer_package():
    """创建安装包（可选，使用 Inno Setup 或其他工具）"""
    print("📦 创建安装包...")
    executable = DIST_DIR / f"{APP_NAME}.exe"
    if not executable.exists():
        print("❌ 找不到可执行文件，跳过安装包创建")
        return False
    
    size = executable.stat().st_size / (1024 * 1024)
    print(f"✅ 可执行文件大小: {size:.2f} MB")
    print(f"📦 可执行文件位置: {executable}")
    print("\n💡 提示: 可以使用 Inno Setup 或 NSIS 创建安装程序")
    return True

def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description=f'构建 {APP_NAME} Windows exe')
    parser.add_argument('--install-deps', action='store_true', 
                       help='自动安装缺失的依赖包')
    args = parser.parse_args()
    
    print(f"🚀 开始构建 {APP_NAME} Windows 64位 exe...")
    
    # 检查平台
    if sys.platform != 'win32':
        print("⚠️  警告: 此脚本设计用于 Windows 平台")
        print("   当前平台:", sys.platform)
        print("   建议在 Windows 环境下运行此脚本")
        # 非交互模式下自动继续（用于CI/CD或自动化构建）
        if not sys.stdin.isatty():
            print("   非交互模式，自动继续...")
        else:
            response = input("   是否继续? (y/n): ")
            if response.lower() != 'y':
                return 1
    
    # 清理旧的构建文件
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    if DIST_DIR.exists():
        # 只清理 PyInstaller 生成的文件，保留其他文件
        for item in DIST_DIR.iterdir():
            if item.name.startswith(APP_NAME) and (item.suffix == '.exe' or item.is_dir()):
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
    
    BUILD_DIR.mkdir(exist_ok=True)
    DIST_DIR.mkdir(exist_ok=True)
    
    # 检查依赖
    if not check_dependencies(auto_install=args.install_deps):
        if not args.install_deps:
            print("\n💡 提示: 使用 --install-deps 参数可自动安装缺失的依赖")
            print("   例如: python build_windows_exe.py --install-deps")
        print("⚠️  依赖检查失败，但继续尝试构建...")
    
    # 构建步骤
    if not build_with_pyinstaller():
        print("❌ 构建失败")
        return 1
    
    if not create_installer_package():
        print("⚠️  安装包创建失败，但 exe 文件已生成")
    
    print("\n🎉 构建完成！")
    print(f"📦 exe 文件位置: {DIST_DIR / f'{APP_NAME}.exe'}")
    return 0

if __name__ == '__main__':
    sys.exit(main())



