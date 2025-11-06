#!/bin/bash
# 构建 QR Viewer AppImage 的脚本

set -e

APP_NAME="QRViewer"
APP_VERSION="1.0.0"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
DIST_DIR="${SCRIPT_DIR}/dist"
APP_DIR="${BUILD_DIR}/${APP_NAME}.AppDir"

echo "🚀 开始构建 ${APP_NAME} AppImage..."

# 清理旧的构建文件
rm -rf "${BUILD_DIR}" "${DIST_DIR}"
mkdir -p "${BUILD_DIR}" "${DIST_DIR}"

# 检查 PyInstaller 是否安装
if ! command -v pyinstaller &> /dev/null; then
    echo "❌ PyInstaller 未安装，正在安装..."
    pip3 install pyinstaller
fi

# 创建 AppDir 目录结构
mkdir -p "${APP_DIR}/usr/bin"
mkdir -p "${APP_DIR}/usr/lib"
mkdir -p "${APP_DIR}/usr/share/applications"
mkdir -p "${APP_DIR}/usr/share/icons/hicolor/256x256/apps"

# 使用 PyInstaller 打包
echo "📦 使用 PyInstaller 打包应用..."
pyinstaller --name="${APP_NAME}" \
    --onefile \
    --windowed \
    --add-data "camera_config.json:config" \
    --hidden-import="tkinter" \
    --hidden-import="tkinter.ttk" \
    --hidden-import="tkinter.filedialog" \
    --hidden-import="cv2" \
    --hidden-import="numpy" \
    --hidden-import="PIL" \
    --hidden-import="PIL.Image" \
    --hidden-import="PIL.ImageTk" \
    --hidden-import="pynng" \
    --hidden-import="turbojpeg" \
    --hidden-import="dynamsoft_barcode_reader_bundle" \
    --collect-all="cv2" \
    --collect-all="numpy" \
    --collect-all="PIL" \
    --collect-all="pynng" \
    --collect-all="turbojpeg" \
    --collect-all="dynamsoft_barcode_reader_bundle" \
    "${SCRIPT_DIR}/qr_gui_viewer.py"

# 检查配置文件是否存在（尝试多个位置）
CONFIG_FILE=""
if [ -f "${SCRIPT_DIR}/camera_config.json" ]; then
    CONFIG_FILE="${SCRIPT_DIR}/camera_config.json"
elif [ -f "${SCRIPT_DIR}/config/camera_config.json" ]; then
    CONFIG_FILE="${SCRIPT_DIR}/config/camera_config.json"
fi

# 创建配置目录
mkdir -p "${APP_DIR}/usr/bin/config"

if [ -n "${CONFIG_FILE}" ]; then
    cp "${CONFIG_FILE}" "${APP_DIR}/usr/bin/config/"
else
    echo "⚠️  warning: camera_config.json 不存在，创建默认配置..."
    cat > "${APP_DIR}/usr/bin/config/camera_config.json" << 'EOF'
{
    "MaxParallelTasks": 8,
    "Timeout": 10000
}
EOF
fi

# 复制可执行文件
if [ -f "${DIST_DIR}/${APP_NAME}" ]; then
    cp "${DIST_DIR}/${APP_NAME}" "${APP_DIR}/usr/bin/"
    chmod +x "${APP_DIR}/usr/bin/${APP_NAME}"
else
    echo "❌ 错误: PyInstaller 构建失败，未找到可执行文件"
    exit 1
fi

# 创建 AppRun 脚本
cat > "${APP_DIR}/AppRun" << 'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
exec "${HERE}/usr/bin/QRViewer" "$@"
EOF
chmod +x "${APP_DIR}/AppRun"

# 创建 .desktop 文件
cat > "${APP_DIR}/usr/share/applications/${APP_NAME}.desktop" << EOF
[Desktop Entry]
Name=${APP_NAME}
Comment=二维码识别上位机界面程序
Exec=QRViewer
Icon=${APP_NAME}
Type=Application
Categories=Utility;
EOF

# 创建图标（如果没有的话）
if [ ! -f "${APP_DIR}/usr/share/icons/hicolor/256x256/apps/${APP_NAME}.png" ]; then
    echo "📝 创建默认图标..."
    # 创建一个简单的图标（可以使用 ImageMagick 或其他工具）
    # 这里先创建一个占位符
    touch "${APP_DIR}/usr/share/icons/hicolor/256x256/apps/${APP_NAME}.png"
fi

# 创建符号链接
ln -sf "usr/share/icons/hicolor/256x256/apps/${APP_NAME}.png" "${APP_DIR}/${APP_NAME}.png"
ln -sf "usr/share/applications/${APP_NAME}.desktop" "${APP_DIR}/${APP_NAME}.desktop"

# 检查并下载 appimagetool
APPIMAGE_TOOL="${BUILD_DIR}/appimagetool.AppImage"
if [ ! -f "${APPIMAGE_TOOL}" ]; then
    echo "📥 下载 appimagetool..."
    wget -O "${APPIMAGE_TOOL}" \
        "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" || {
        echo "❌ 下载 appimagetool 失败，尝试使用本地版本..."
        if ! command -v appimagetool &> /dev/null; then
            echo "❌ 请手动安装 appimagetool"
            exit 1
        fi
        APPIMAGE_TOOL="appimagetool"
    }
    chmod +x "${APPIMAGE_TOOL}"
fi

# 生成 AppImage
echo "🎨 生成 AppImage..."
if [ -f "${APPIMAGE_TOOL}" ]; then
    "${APPIMAGE_TOOL}" "${APP_DIR}" "${DIST_DIR}/${APP_NAME}-${APP_VERSION}-x86_64.AppImage"
else
    "${APPIMAGE_TOOL}" "${APP_DIR}" "${DIST_DIR}/${APP_NAME}-${APP_VERSION}-x86_64.AppImage"
fi

echo "✅ AppImage 构建完成: ${DIST_DIR}/${APP_NAME}-${APP_VERSION}-x86_64.AppImage"
echo "📦 文件大小: $(du -h "${DIST_DIR}/${APP_NAME}-${APP_VERSION}-x86_64.AppImage" | cut -f1)"

