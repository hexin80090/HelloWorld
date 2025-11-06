# 构建 QR Viewer AppImage 说明

本目录包含用于构建 QR Viewer AppImage 的脚本。

## 前置要求

1. **Python 3.x** 和必需的库：
   ```bash
   pip3 install PyInstaller opencv-python numpy pillow pynng turbojpeg dynamsoft-barcode-reader-bundle
   ```

2. **appimagetool**（脚本会自动下载，也可以手动安装）

## 构建方法

### 方法 1: 使用 Python 脚本（推荐）

```bash
python3 build_appimage.py
```

这个脚本会：
- 自动检查依赖
- 使用 PyInstaller 打包应用
- 创建 AppDir 结构
- 生成 AppImage

### 方法 2: 使用 Shell 脚本

```bash
./build_appimage.sh
```

## 输出

构建完成后，AppImage 文件会在 `dist/` 目录下：

```
dist/QRViewer-1.0.0-x86_64.AppImage
```

## 使用方法

1. **赋予执行权限**：
   ```bash
   chmod +x QRViewer-1.0.0-x86_64.AppImage
   ```

2. **运行**：
   ```bash
   ./QRViewer-1.0.0-x86_64.AppImage
   ```

3. **或者双击运行**（在文件管理器中）

## 命令行参数

AppImage 支持与原始脚本相同的命令行参数：

```bash
./QRViewer-1.0.0-x86_64.AppImage --host 0.0.0.0 --client 192.168.0.104 --dbr
```

- `--host`: 指定监听IP地址
- `--client`: 指定相机节点IP地址
- `--dbr`: 启用内置DBR识别

## 注意事项

1. **配置文件**: 如果 `camera_config.json` 不存在，构建脚本会自动创建一个默认配置。

2. **图标**: 当前使用占位符图标。如果需要自定义图标，请将 `QRViewer.png` (256x256) 放在项目根目录，构建脚本会自动使用它。

3. **依赖库**: 所有 Python 依赖都会打包到 AppImage 中，目标电脑不需要安装 Python。

4. **文件大小**: AppImage 可能较大（几百MB），因为包含了所有依赖库。

## 故障排除

### PyInstaller 构建失败

- 确保所有依赖都已正确安装
- 检查 Python 版本兼容性
- 查看 `build/` 目录下的错误日志

### AppImage 无法运行

- 检查文件权限：`chmod +x QRViewer-1.0.0-x86_64.AppImage`
- 在终端运行查看错误信息
- 确保目标系统是 x86_64 Linux

### 缺少库错误

- 检查 `build_appimage.py` 中的 `--collect-all` 参数
- 手动添加缺少的模块到隐藏导入列表

## 在其他电脑上使用

1. 将 `QRViewer-1.0.0-x86_64.AppImage` 复制到目标电脑
2. 赋予执行权限：`chmod +x QRViewer-1.0.0-x86_64.AppImage`
3. 直接运行即可，无需安装 Python 或任何依赖


