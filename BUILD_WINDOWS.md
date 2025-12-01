# Windows 64位 exe 构建说明

## 环境要求

1. **Windows 操作系统**（Windows 10/11 64位）
2. **Python 3.10+**（64位版本）
3. **必需的 Python 包**（构建脚本可自动安装）

## 构建步骤

### 方法 1: 使用构建脚本（推荐）

1. 在 Windows 环境下，打开命令提示符或 PowerShell
2. 进入项目目录：
   ```cmd
   cd C:\path\to\QR
   ```
3. **自动安装依赖并构建**（推荐）：
   ```cmd
   python build_windows_exe.py --install-deps
   ```
   或者**手动安装依赖后构建**：
   ```cmd
   pip install -r requirements_build.txt
   python build_windows_exe.py
   ```

### 方法 2: 手动使用 PyInstaller

1. 确保所有依赖已安装
2. 运行 PyInstaller 命令：
   ```cmd
   pyinstaller --name QRViewer --onefile --windowed --add-data=camera_config.json;config --hidden-import=tkinter --hidden-import=tkinter.ttk --hidden-import=tkinter.filedialog --hidden-import=cv2 --hidden-import=numpy --hidden-import=PIL --hidden-import=PIL.Image --hidden-import=PIL.ImageTk --hidden-import=pynng --hidden-import=turbojpeg --hidden-import=dynamsoft_barcode_reader_bundle --collect-all=cv2 --collect-all=numpy --collect-all=PIL --collect-all=pynng --collect-all=turbojpeg --collect-all=dynamsoft_barcode_reader_bundle qr_gui_viewer.py
   ```

## 输出文件

构建完成后，可执行文件将位于：
- `dist/QRViewer.exe`

## 配置文件

配置文件 `camera_config.json` 会被打包到 exe 中，路径为：
- 打包资源路径：`{_MEIPASS}/config/camera_config.json`
- 工作目录：`{exe所在目录}/config/camera_config.json`

程序会自动查找配置文件，优先级：
1. 打包资源路径
2. exe 所在目录的 config 子目录
3. 当前工作目录

## 日志文件

日志文件会保存在 exe 所在目录的 `test_results` 子目录中。

## 注意事项

1. **TurboJPEG 库路径**：
   - Windows 环境下，TurboJPEG 库路径可能需要特殊处理
   - 如果遇到问题，请确保 `libturbojpeg.dll` 在系统 PATH 中或与 exe 在同一目录

2. **防病毒软件**：
   - 某些防病毒软件可能会误报 PyInstaller 打包的 exe
   - 如果遇到问题，请将 exe 添加到防病毒软件的白名单

3. **依赖库**：
   - 确保所有依赖库都是 64 位版本
   - OpenCV、NumPy 等库需要与 Python 版本匹配

## 创建安装程序（可选）

可以使用以下工具创建安装程序：

1. **Inno Setup**（推荐）：
   - 下载：https://jrsoftware.org/isdl.php
   - 创建安装脚本，包含 exe 和必要的配置文件

2. **NSIS**：
   - 下载：https://nsis.sourceforge.io/
   - 功能强大的安装程序制作工具

## 故障排除

### 问题 1: 找不到模块
**解决方案**：确保所有依赖都已正确安装，使用 `pip list` 检查

### 问题 2: exe 文件太大
**解决方案**：这是正常的，因为包含了所有依赖库。可以使用 `--exclude-module` 排除不需要的模块

### 问题 3: 运行时错误
**解决方案**：
- 检查是否有缺失的 DLL 文件
- 使用 `--debug=all` 参数重新构建以获取详细错误信息
- 检查 Windows 事件查看器中的错误日志

## 测试

构建完成后，建议在干净的 Windows 环境中测试 exe 文件，确保所有功能正常。



