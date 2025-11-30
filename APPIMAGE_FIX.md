# AppImage 与脚本运行差异问题修复

## 问题描述

编译出来的 AppImage 和直接运行脚本的结果不一样，主要原因是路径处理不一致。

## 问题原因

1. **配置文件路径问题**：
   - 脚本运行时：`__file__` 指向脚本文件路径
   - AppImage 打包后：`__file__` 指向临时解压目录（`_MEIPASS`），路径解析会出错

2. **工作目录问题**：
   - `test_results` 目录的路径在不同环境下不一致
   - 打包后需要使用可执行文件所在目录，而不是临时目录

## 解决方案

### 1. 添加路径处理函数

在 `qr_gui_viewer.py` 中添加了三个辅助函数：

- `get_resource_path(relative_path)`: 获取打包资源路径，兼容脚本和打包环境
- `get_config_path()`: 获取配置文件路径，尝试多个可能的路径
- `get_work_dir()`: 获取工作目录，用于存放 `test_results` 等文件

### 2. 修改路径引用

将所有硬编码的路径引用改为使用新的辅助函数：

- 配置文件加载：使用 `get_config_path()`
- 日志文件目录：使用 `get_work_dir() + 'test_results'`
- DBR 日志文件：使用 `get_work_dir() + 'test_results'`

### 3. 路径查找策略

配置文件查找顺序：
1. PyInstaller 打包资源路径：`{_MEIPASS}/config/camera_config.json`
2. 开发环境路径：`{项目根目录}/config/camera_config.json`
3. 同级目录：`{脚本目录}/config/camera_config.json`
4. 当前目录：`camera_config.json`

## 使用方法

### 重新生成 AppImage

```bash
python3 build_appimage.py
```

### 验证修复

1. 运行脚本版本：
   ```bash
   python3 qr_gui_viewer.py --dbr
   ```

2. 运行 AppImage 版本：
   ```bash
   ./dist/QRViewer-1.0.0-x86_64.AppImage --dbr
   ```

3. 检查两个版本的行为是否一致：
   - 配置文件是否正确加载
   - `test_results` 目录是否在正确位置创建
   - 日志文件是否正常写入

## 注意事项

1. **配置文件位置**：
   - 开发环境：`/home/hexin/QR/config/camera_config.json` 或 `/home/hexin/config/camera_config.json`
   - AppImage：配置文件被打包到资源中，路径为 `{_MEIPASS}/config/camera_config.json`

2. **工作目录**：
   - 开发环境：`/home/hexin/QR/test_results/`
   - AppImage：`{AppImage所在目录}/test_results/`

3. **日志文件**：
   - 所有日志文件都会写入到工作目录下的 `test_results` 目录
   - 如果目录不存在，会自动创建


