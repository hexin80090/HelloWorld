#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
二维码识别上位机界面程序 - 集成版
集成了接收、显示、识别功能，一个程序完成所有功能
采用simple_receiver.py的OpenCV界面显示图片
按照文档设计：区域1（统计）、区域2（最终识别结果）、区域3（图片-OpenCV窗口）、区域4（每次识别结果）
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
import cv2
import numpy as np
from PIL import Image, ImageTk
import threading
import queue
import csv
import os
import time
import json
from datetime import datetime
from collections import defaultdict
import pynng
import pynng.exceptions as nng_exceptions
from turbojpeg import TurboJPEG
from dynamsoft_barcode_reader_bundle import *


class QRViewerGUI:
    def __init__(self, root, listen_host=None, camera_ip=None, enable_dbr=False):
        self.root = root
        self.root.title("二维码识别结果展示系统")
        self.root.geometry("1600x1000")
        
        # 配置参数（从命令行参数获取）
        self.listen_host = listen_host if listen_host else '0.0.0.0'
        self.listen_port = 5555
        self.camera_node_ip = camera_ip if camera_ip else '192.168.0.176'
        self.ack_port = 5556
        self.dbr_enabled = bool(enable_dbr)
        
        # 加载配置文件
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'camera_config.json')
        self.config = self._load_config(config_path)
        self.dbr_thread_count = self.config.get('MaxParallelTasks', 8)
        self.dbr_timeout = self.config.get('Timeout', 10000)
        
        # 识别结果数据
        self.recognition_results = []  # 原始DBR log格式数据
        self.summary_data = {}  # 汇总数据（商品信息等）
        self.dbr_log_columns = ['global_seq', 'recv_seq', 'worker_id', 'slot_status', 'position', 'format', 'text']
        self.summary_columns = ['序号', '商品信息', '识数量', '库存数量', '批次', '货架']
        
        # 统计信息
        self.stats = {
            'total_images': 0,
            'total_recognitions': 0,
            'qr_code_count': 0,
            'barcode_count': 0,
            'success_rate': 0.0,
            'last_update': None,
            'tcp_connected': False
        }
        
        # 日志文件监听
        self.log_file_path = None
        self.last_log_position = 0
        
        # NNG接收器（服务器模式，接收数据）
        self.nng_subscriber = None
        self.received_count = 0
        self.last_successful_receive = 0
        self.current_frame_sequence = 0
        self.recv_seq_counter = 0
        
        # ACK发送器
        self.ack_sender = None
        
        # DBR相关
        self.dbr_queue = None
        self.dbr_threads = []
        self.dbr_log_file = None
        self.dbr_global_seq = 0
        self.dbr_dropped_frames = 0
        self.dbr_total_time_ms = 0.0
        self.dbr_total_attempts = 0
        self.dbr_total_decoded = 0
        self.dbr_stats_lock = threading.Lock()
        
        # OpenCV显示相关（从simple_receiver.py集成）
        self.running = True
        self.slot_num = 200
        self.crops_buffer = [None] * self.slot_num
        self.write_index = 0
        self.read_index = -1
        self.latest_index = -1
        self.locked_latest_index = -1
        self.first_crop = True
        self.base_round_duration = 0.03
        self.last_switch_time = 0
        self.delta = 0
        self.locked_delta = 0
        self.left_arrow_rect = None
        self.right_arrow_rect = None
        
        # 初始化TurboJPEG
        try:
            self.jpeg = TurboJPEG()
        except Exception as e:
            if os.name == 'nt':
                try:
                    self.jpeg = TurboJPEG(r"C:\libjpeg-turbo64\bin\libturbojpeg.dll")
                except:
                    print(f"❌ TurboJPEG初始化失败: {e}")
                    raise
            else:
                print(f"❌ TurboJPEG初始化失败: {e}")
                raise
        
        # 初始化NNG服务器和DBR（在UI创建之前）
        self._init_nng_server()
        self._init_ack_sender()
        if self.dbr_enabled:
            self._init_dbr()
        
        # 设置鼠标回调函数（OpenCV窗口用）
        self.setup_mouse_callback()
        
        # 创建UI
        self.create_widgets()
        
        # 绑定窗口大小变化事件，使图片区域保持正方形
        self.root.bind('<Configure>', self.on_window_configure)
        
        # 启动更新线程
        self.start_update_threads()
        
        # 延迟设置初始正方形大小（等窗口完全初始化后）
        self.root.after(100, self.adjust_image_size)
    
    def create_widgets(self):
        """创建GUI组件 - 按照新布局设计：
        上部分：区域3（图片，左）和区域2（最终识别结果，右），等高
        下部分：区域1（统计信息，左）和区域4（每次识别结果，右），等高
        """
        # 主容器 - 垂直分割（上部分和下部分）
        main_paned = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # === 上部分：区域3（图片，左，正方形）和区域2（最终识别结果，右），等高 ===
        top_paned = ttk.PanedWindow(main_paned, orient=tk.HORIZONTAL)
        main_paned.add(top_paned, weight=1)
        self.top_paned = top_paned  # 保存引用以便调整分割位置
        
        # === 区域3：图片显示（左侧，正方形）===
        image_frame = ttk.LabelFrame(top_paned, text="图片", padding=5)
        top_paned.add(image_frame, weight=0)  # weight=0，手动控制大小
        self.create_image_panel(image_frame)
        self.image_frame = image_frame  # 保存引用
        
        # === 区域2：最终识别结果（右侧，占据剩余宽度）===
        final_result_frame = ttk.LabelFrame(top_paned, text="最终识别结果", padding=5)
        top_paned.add(final_result_frame, weight=1)
        self.create_final_result_panel(final_result_frame)
        
        # === 下部分：区域1（统计信息，左）和区域4（每次识别结果，右），等高 ===
        bottom_paned = ttk.PanedWindow(main_paned, orient=tk.HORIZONTAL)
        main_paned.add(bottom_paned, weight=1)
        self.bottom_paned = bottom_paned  # 保存引用以便调整分割位置
        
        # === 区域1：统计信息和导出按钮（左侧，无外框，与图片等宽）===
        left_frame = ttk.Frame(bottom_paned, padding=(5, 0, 5, 5))  # 上边距为0，与右侧对齐
        bottom_paned.add(left_frame, weight=0)  # weight=0，手动控制宽度与图片等宽
        self.create_statistics_panel(left_frame)
        self.left_frame = left_frame  # 保存引用
        
        # === 区域4：每次识别结果（DBR Log格式，右侧）===
        log_result_frame = ttk.LabelFrame(bottom_paned, text="每次的识别结果", padding=5)
        bottom_paned.add(log_result_frame, weight=1)
        self.create_log_result_panel(log_result_frame)
        
    
    def create_statistics_panel(self, parent):
        """创建区域1：统计信息面板"""
        # 统计信息显示（可以自适应高度，与右侧对齐）
        stats_label_frame = ttk.LabelFrame(parent, text="统计信息", padding=5)
        stats_label_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 2))  # 上边距为0，与右侧LabelFrame对齐
        
        # 成功率
        success_rate_frame = ttk.Frame(stats_label_frame)
        success_rate_frame.pack(fill=tk.X, pady=5)
        ttk.Label(success_rate_frame, text="成功率:", font=('Arial', 11)).pack(side=tk.LEFT, padx=5)
        self.success_rate_var = tk.StringVar(value="0.00%")
        ttk.Label(success_rate_frame, textvariable=self.success_rate_var, font=('Arial', 11, 'bold')).pack(side=tk.LEFT)
        
        # 总识别
        total_frame = ttk.Frame(stats_label_frame)
        total_frame.pack(fill=tk.X, pady=5)
        ttk.Label(total_frame, text="总识别:", font=('Arial', 11)).pack(side=tk.LEFT, padx=5)
        self.total_var = tk.StringVar(value="0")
        ttk.Label(total_frame, textvariable=self.total_var, font=('Arial', 11, 'bold')).pack(side=tk.LEFT)
        
        # 二维码识别
        qr_frame = ttk.Frame(stats_label_frame)
        qr_frame.pack(fill=tk.X, pady=5)
        ttk.Label(qr_frame, text="二维码识别:", font=('Arial', 11)).pack(side=tk.LEFT, padx=5)
        self.qr_var = tk.StringVar(value="0")
        ttk.Label(qr_frame, textvariable=self.qr_var, font=('Arial', 11, 'bold')).pack(side=tk.LEFT)
        
        # 条形码识别
        barcode_frame = ttk.Frame(stats_label_frame)
        barcode_frame.pack(fill=tk.X, pady=5)
        ttk.Label(barcode_frame, text="条形码识别:", font=('Arial', 11)).pack(side=tk.LEFT, padx=5)
        self.barcode_var = tk.StringVar(value="0")
        ttk.Label(barcode_frame, textvariable=self.barcode_var, font=('Arial', 11, 'bold')).pack(side=tk.LEFT)
        
        # CSV按钮（导出和导入）
        csv_frame = ttk.Frame(parent)
        csv_frame.pack(pady=5, fill=tk.X)
        csv_export_btn = ttk.Button(csv_frame, text="导出CSV", command=self.export_to_csv)
        csv_export_btn.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        csv_import_btn = ttk.Button(csv_frame, text="导入CSV", command=self.import_from_csv)
        csv_import_btn.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        
        # 控制面板（减少padding，与右侧对齐）
        control_frame = ttk.LabelFrame(parent, text="控制", padding=5)
        control_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 5))
        
        # 日志文件选择（对齐到统计信息的padx=5）
        log_frame = ttk.Frame(control_frame)
        log_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(log_frame, text="日志文件:").pack(side=tk.LEFT, padx=5)
        self.log_path_var = tk.StringVar(value="test_results/dbr_multithread_result_*.log")
        log_entry = ttk.Entry(log_frame, textvariable=self.log_path_var)
        log_entry.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)  # 自适应宽度，填充剩余空间
        
        browse_btn = ttk.Button(log_frame, text="浏览", command=self.browse_log_file, width=6)
        browse_btn.pack(side=tk.LEFT, padx=2)
        
        # 自动刷新选项
        self.auto_refresh_var = tk.BooleanVar(value=True)
        auto_refresh_cb = ttk.Checkbutton(
            control_frame,
            text="自动刷新日志文件",
            variable=self.auto_refresh_var
        )
        auto_refresh_cb.pack(anchor=tk.W, pady=2, padx=5)
        
        # 自动查找最新日志文件
        self.auto_find_latest_var = tk.BooleanVar(value=True)
        auto_find_cb = ttk.Checkbutton(
            control_frame,
            text="自动查找最新日志文件",
            variable=self.auto_find_latest_var
        )
        auto_find_cb.pack(anchor=tk.W, pady=2, padx=5)
    
    def create_final_result_panel(self, parent):
        """创建区域2：最终识别结果面板（使用表格显示）"""
        # 表格框架（直接显示表格，不显示标题）
        table_frame = ttk.Frame(parent)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Treeview表格（最终识别结果使用summary_tree）
        self.summary_tree = ttk.Treeview(
            table_frame,
            columns=self.summary_columns,
            show='headings',
            height=15
        )
        
        # 定义列
        column_widths = {
            '序号': 60,
            '商品信息': 200,
            '识数量': 80,
            '库存数量': 100,
            '批次': 100,
            '货架': 100
        }
        
        for col in self.summary_columns:
            self.summary_tree.heading(col, text=col)
            self.summary_tree.column(col, width=column_widths.get(col, 100), anchor=tk.W)
        
        # 滚动条
        scrollbar_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.summary_tree.yview)
        scrollbar_x = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.summary_tree.xview)
        self.summary_tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        
        # 布局
        self.summary_tree.grid(row=0, column=0, sticky='nsew')
        scrollbar_y.grid(row=0, column=1, sticky='ns')
        scrollbar_x.grid(row=1, column=0, sticky='ew')
        
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        
        # 初始数据
        self.update_summary_table()
    
    def create_image_panel(self, parent):
        """创建区域3：图片显示面板 - 集成OpenCV图片显示到Tkinter"""
        # 创建Canvas用于显示图片
        self.image_canvas = tk.Canvas(parent, bg='black', highlightthickness=0)
        self.image_canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 绑定鼠标点击事件
        self.image_canvas.bind("<Button-1>", self.on_image_click)
        
        # 绑定键盘事件
        self.image_canvas.bind("<Key>", self.on_key_press)
        self.image_canvas.focus_set()  # 让Canvas可以接收键盘事件
        
        # 初始显示提示信息
        self.show_image_placeholder()
    
    def show_image_placeholder(self):
        """显示图片占位符"""
        self.image_canvas.delete("all")
        self.image_canvas.create_text(
            self.image_canvas.winfo_width()//2, 
            self.image_canvas.winfo_height()//2,
            text="等待图片数据...\n\n按ESC退出，方向键翻页，空格手动识别",
            fill="white",
            font=('Arial', 12),
            justify=tk.CENTER
        )
    
    def on_image_click(self, event):
        """处理图片区域的鼠标点击事件"""
        # 模拟simple_receiver.py的鼠标点击处理
        x, y = event.x, event.y
        canvas_width = self.image_canvas.winfo_width()
        canvas_height = self.image_canvas.winfo_height()
        
        # 检查是否点击在左箭头区域
        if self.left_arrow_rect and self.is_point_in_rect(x, y, self.left_arrow_rect):
            N = min(1000, self.received_count)
            if self.delta > (1 - N):
                self.delta -= 1
                self.update_image_display()
        
        # 检查是否点击在右箭头区域
        elif self.right_arrow_rect and self.is_point_in_rect(x, y, self.right_arrow_rect):
            if self.delta < 0:
                self.delta += 1
                self.update_image_display()
    
    def update_image_display(self):
        """更新图片显示"""
        if not hasattr(self, 'image_canvas'):
            return
            
        # 获取当前要显示的照片
        display_index = (self.read_index + self.locked_delta) % self.slot_num
        current_crop = self.crops_buffer[display_index]
        
        if not current_crop:
            self.show_image_placeholder()
            return
        
        try:
            # 解码JPEG数据
            img_data = current_crop['image_data']
            bgr_image = self.jpeg.decode(img_data)
            
            if bgr_image is None or not isinstance(bgr_image, np.ndarray):
                self.show_image_placeholder()
                return
            
            # 转换为RGB
            rgb_image = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)
            
            # 获取Canvas尺寸
            canvas_width = self.image_canvas.winfo_width()
            canvas_height = self.image_canvas.winfo_height()
            
            if canvas_width <= 1 or canvas_height <= 1:
                return  # Canvas还没有初始化
            
            # 计算缩放比例
            img_height, img_width = rgb_image.shape[:2]
            scale = min(canvas_width/img_width, canvas_height/img_height)
            new_width = int(img_width * scale)
            new_height = int(img_height * scale)
            
            # 缩放图片
            resized_image = cv2.resize(rgb_image, (new_width, new_height))
            
            # 转换为PIL Image
            pil_image = Image.fromarray(resized_image)
            
            # 转换为Tkinter PhotoImage
            photo = ImageTk.PhotoImage(pil_image)
            
            # 清除Canvas并显示图片
            self.image_canvas.delete("all")
            x = (canvas_width - new_width) // 2
            y = (canvas_height - new_height) // 2
            self.image_canvas.create_image(x, y, anchor=tk.NW, image=photo)
            
            # 保存引用防止垃圾回收
            self.image_canvas.image = photo
            
            # 添加信息覆盖层
            self.draw_image_overlay(current_crop, canvas_width, canvas_height)
            
        except Exception as e:
            print(f"图片显示错误: {e}")
            self.show_image_placeholder()
    
    def draw_image_overlay(self, current_crop, canvas_width, canvas_height):
        """在图片上绘制信息覆盖层"""
        try:
            metadata = current_crop['metadata']
            
            # 基础信息
            frame_id = current_crop.get('frame_sequence', 0)
            display_index = (self.read_index + self.locked_delta) % self.slot_num
            info_text = f"Frame:{frame_id} | Index:{display_index} | Total:{self.received_count}"
            
            # 在Canvas上绘制文本
            self.image_canvas.create_text(
                10, 20, 
                text=info_text, 
                fill="lime", 
                font=('Arial', 10, 'bold'),
                anchor=tk.W
            )
            
            # TCP连接状态
            status_color = "lime" if self.tcp_connected else "red"
            status_text = "TCP: 连接" if self.tcp_connected else "TCP: 断开"
            self.image_canvas.create_text(
                canvas_width - 10, 20, 
                text=status_text, 
                fill=status_color, 
                font=('Arial', 10, 'bold'),
                anchor=tk.E
            )
            
            # 检测信息
            roi_info = metadata.get('roi', {})
            label = roi_info.get('label', 'unknown')
            confidence = roi_info.get('confidence', 0.0)
            pose_info = metadata.get('pose', {})
            position_array = pose_info.get('position', [0.0, 0.0, 0.0])
            
            if len(position_array) >= 3:
                px = f"{position_array[0]:.2f}"
                py = f"{position_array[1]:.2f}"
                pz = f"{position_array[2]:.2f}"
                detection_text = f"({px},{py},{pz}) {label} | Conf: {confidence:.3f}"
            else:
                detection_text = f"{label} | Conf: {confidence:.3f}"
            
            self.image_canvas.create_text(
                10, 50, 
                text=detection_text, 
                fill="yellow", 
                font=('Arial', 9),
                anchor=tk.W
            )
            
            # DBR识别结果
            dbr_items = current_crop.get('dbr_items')
            dbr_elapsed = current_crop.get('dbr_elapsed_ms')
            if dbr_items:
                elapsed_text = f"DBR: {float(dbr_elapsed):.1f} ms"
                self.image_canvas.create_text(
                    10, canvas_height - 40, 
                    text=elapsed_text, 
                    fill="cyan", 
                    font=('Arial', 9),
                    anchor=tk.W
                )
                
                # 显示前2个结果
                for i, item in enumerate(dbr_items[:2]):
                    fmt = item.get('fmt', 'UNK')
                    txt = item.get('text', '')
                    line = f"[{fmt}] {txt}"
                    y = canvas_height - 20 + i * 15
                    self.image_canvas.create_text(
                        10, y, 
                        text=line, 
                        fill="cyan", 
                        font=('Arial', 8),
                        anchor=tk.W
                    )
            
            # 控制提示
            control_text = "ESC=Quit, SPACE=Manual DBR, ←→=Navigate"
            self.image_canvas.create_text(
                canvas_width - 10, canvas_height - 10, 
                text=control_text, 
                fill="white", 
                font=('Arial', 8),
                anchor=tk.SE
            )
            
        except Exception as e:
            print(f"覆盖层绘制错误: {e}")
    
    def on_key_press(self, event):
        """处理键盘事件"""
        key = event.keysym
        if key == "Escape":
            self.running = False
            self.root.quit()
        elif key == "space":
            self.manual_dbr_trigger()
        elif key == "Left":
            N = min(self.slot_num, self.received_count)
            if self.delta > (1 - N):
                self.delta -= 1
                self.update_image_display()
        elif key == "Right":
            if self.delta < 0:
                self.delta += 1
                self.update_image_display()
    
    def create_log_result_panel(self, parent):
        """创建区域4：每次识别结果面板（DBR Log格式）"""
        # 表格框架（直接显示表格，不显示标题说明）
        table_frame = ttk.Frame(parent)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Treeview表格
        self.log_result_tree = ttk.Treeview(
            table_frame,
            columns=self.dbr_log_columns,
            show='headings',
            height=8
        )
        
        # 定义列 - 计算表头文字宽度，确保列宽至少能显示完整表头
        column_headings = {
            'global_seq': 'Global Seq',
            'recv_seq': 'Recv Seq',
            'worker_id': 'Worker Id',
            'slot_status': 'Slot Status',
            'position': 'Position',
            'format': 'Format',
            'text': 'Text'
        }
        
        # 先计算每列表头文字需要的最小宽度
        min_column_widths = {}
        for col in self.dbr_log_columns:
            heading = column_headings.get(col, col.replace('_', ' ').title())
            # 估算：每个字符约8-10像素，英文约8像素，中文约12像素
            min_width = max(len(heading) * 10, 80)  # 至少80像素，确保表头完整显示
            min_column_widths[col] = min_width
        
        # 设置列标题和宽度
        # text列分配更多宽度（因为内容较长），其他列尽量等宽
        for col in self.dbr_log_columns:
            heading = column_headings.get(col, col.replace('_', ' ').title())
            self.log_result_tree.heading(col, text=heading)
            if col == 'text':
                # text列使用更大的宽度（内容通常较长）
                col_width = max(min_column_widths[col], 400)
            elif col == 'position':
                # position列稍微宽一点（因为有括号和逗号）
                col_width = max(min_column_widths[col], 120)
            else:
                # 其他列使用统一的最小宽度，保持等宽效果
                col_width = max(min_column_widths[col], 90)
            self.log_result_tree.column(col, width=col_width, anchor=tk.W, minwidth=min_column_widths[col])
        
        # 滚动条
        scrollbar_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.log_result_tree.yview)
        scrollbar_x = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.log_result_tree.xview)
        self.log_result_tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        
        # 布局
        self.log_result_tree.grid(row=0, column=0, sticky='nsew')
        scrollbar_y.grid(row=0, column=1, sticky='ns')
        scrollbar_x.grid(row=1, column=0, sticky='ew')
        
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
    
    def create_summary_table_panel(self, parent):
        """创建底部条形码记录汇总表格"""
        # 标题
        title_label = ttk.Label(parent, text="条形码记录", font=('Arial', 10, 'bold'))
        title_label.pack(anchor=tk.W, padx=5, pady=5)
        
        # 表格框架
        table_frame = ttk.Frame(parent)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Treeview表格
        self.summary_tree = ttk.Treeview(
            table_frame,
            columns=self.summary_columns,
            show='headings',
            height=6
        )
        
        # 定义列
        column_widths = {
            '序号': 60,
            '商品信息': 200,
            '识数量': 80,
            '库存数量': 100,
            '批次': 100,
            '货架': 100
        }
        
        for col in self.summary_columns:
            self.summary_tree.heading(col, text=col)
            self.summary_tree.column(col, width=column_widths.get(col, 100), anchor=tk.W)
        
        # 滚动条
        scrollbar_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.summary_tree.yview)
        scrollbar_x = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.summary_tree.xview)
        self.summary_tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        
        # 布局
        self.summary_tree.grid(row=0, column=0, sticky='nsew')
        scrollbar_y.grid(row=0, column=1, sticky='ns')
        scrollbar_x.grid(row=1, column=0, sticky='ew')
        
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        
        # 初始数据
        self.update_summary_table()
    
    def browse_log_file(self):
        """浏览日志文件"""
        filename = filedialog.askopenfilename(
            title="选择DBR日志文件",
            filetypes=[("Log files", "*.log"), ("All files", "*.*")],
            initialdir=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_results')
        )
        if filename:
            self.log_path_var.set(filename)
            self.load_log_file(filename)
    
    def load_current_log_file(self):
        """加载当前指定的日志文件"""
        log_path = self.log_path_var.get()
        if log_path and os.path.exists(log_path):
            self.load_log_file(log_path)
        else:
            self.update_final_result(f"日志文件不存在: {log_path}")
    
    def find_latest_log_file(self):
        """查找最新的日志文件"""
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_results')
        if not os.path.exists(log_dir):
            return None
        
        log_files = []
        for f in os.listdir(log_dir):
            if f.startswith('dbr_multithread_result_') and f.endswith('.log'):
                filepath = os.path.join(log_dir, f)
                log_files.append((os.path.getmtime(filepath), filepath))
        
        if log_files:
            log_files.sort(reverse=True)
            return log_files[0][1]
        return None
    
    def load_log_file(self, filepath):
        """加载日志文件"""
        if not os.path.exists(filepath):
            self.update_final_result(f"日志文件不存在: {filepath}")
            return
        
        try:
            self.log_file_path = filepath
            self.last_log_position = 0
            
            # 清空现有数据
            self.recognition_results.clear()
            self.summary_data.clear()
            
            # 清空表格
            for item in self.log_result_tree.get_children():
                self.log_result_tree.delete(item)
            
            # 读取现有数据
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        self.parse_and_add_result(line)
            
            # 更新UI
            self.update_statistics()
            self.update_summary_table()
            self.update_final_result(f"已加载日志文件: {os.path.basename(filepath)}\n共 {len(self.recognition_results)} 条记录")
        except Exception as e:
            self.update_final_result(f"加载日志文件失败: {e}")
    
    def parse_and_add_result(self, line):
        """解析并添加识别结果"""
        try:
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 7:
                result = {
                    'global_seq': parts[0],
                    'recv_seq': parts[1],
                    'worker_id': parts[2],
                    'slot_status': parts[3],
                    'position': parts[4],
                    'format': parts[5],
                    'text': ','.join(parts[6:])
                }
                self.recognition_results.append(result)
                self.add_result_to_log_tree(result)
                
                # 统计
                self.stats['total_recognitions'] += 1
                if 'QR' in result['format'].upper() or 'QR_CODE' in result['format'].upper():
                    self.stats['qr_code_count'] += 1
                else:
                    self.stats['barcode_count'] += 1
                
                # 更新汇总数据（根据text字段解析商品信息）
                self.update_summary_data(result)
        except Exception as e:
            print(f"解析结果行失败: {e}, 行: {line}")
    
    def update_summary_data(self, result):
        """更新汇总数据（解析商品信息等）"""
        text = result.get('text', '')
        # 简化处理：使用text的前50个字符作为商品信息key
        # 实际应用中需要根据具体的text格式来解析商品信息
        if text.startswith('HTTPS://') or text.startswith('HTTP://'):
            # 如果是URL，提取关键部分
            product_key = text.split('/')[-1][:50] if '/' in text else text[:50]
        else:
            product_key = text[:50]
        
        if product_key not in self.summary_data:
            self.summary_data[product_key] = {
                '商品信息': product_key if len(product_key) < 50 else product_key[:47] + '...',
                '识数量': 0,
                '库存数量': '未找到库存信息',
                '批次': '',
                '货架': ''
            }
        
        self.summary_data[product_key]['识数量'] += 1
    
    def add_result_to_log_tree(self, result):
        """添加结果到日志表格"""
        values = [result.get(col, '') for col in self.dbr_log_columns]
        self.log_result_tree.insert('', tk.END, values=values)
        if self.log_result_tree.get_children():
            self.log_result_tree.see(self.log_result_tree.get_children()[-1])
    
    def update_statistics(self):
        """更新统计信息"""
        total = self.stats['total_recognitions']
        if total > 0:
            # 计算成功率（假设识数量>0即为成功）
            successful = sum(1 for r in self.recognition_results if r.get('text', '').strip())
            self.stats['success_rate'] = (successful / total) * 100
        else:
            self.stats['success_rate'] = 0.0
        
        self.success_rate_var.set(f"{self.stats['success_rate']:.2f}%")
        self.total_var.set(str(total))
        self.qr_var.set(str(self.stats['qr_code_count']))
        self.barcode_var.set(str(self.stats['barcode_count']))
    
    def update_summary_table(self):
        """更新汇总表格"""
        # 清空现有数据
        for item in self.summary_tree.get_children():
            self.summary_tree.delete(item)
        
        # 添加汇总数据
        for idx, (key, data) in enumerate(sorted(self.summary_data.items()), 1):
            values = [
                str(idx),
                data['商品信息'],
                str(data['识数量']),
                str(data['库存数量']),
                data['批次'],
                data['货架']
            ]
            self.summary_tree.insert('', tk.END, values=values)
    
    def update_final_result(self, message):
        """更新最终识别结果显示（现在通过表格显示，这里保留用于日志）"""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
    
    def update_image(self, image, metadata=None):
        """更新显示的图像"""
        if image is None:
            return
        
        self.current_image = image
        self.current_metadata = metadata
        
        # 转换为PIL图像
        if isinstance(image, np.ndarray):
            if len(image.shape) == 3 and image.shape[2] == 3:
                image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            else:
                image_rgb = image
            pil_image = Image.fromarray(image_rgb)
        else:
            pil_image = image
        
        # 获取Canvas大小
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        
        if canvas_width > 1 and canvas_height > 1:
            img_width, img_height = pil_image.size
            scale = min(canvas_width / img_width, canvas_height / img_height)
            new_width = int(img_width * scale)
            new_height = int(img_height * scale)
            pil_image = pil_image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # 转换为PhotoImage
        self.photo = ImageTk.PhotoImage(image=pil_image)
        
        # 更新Canvas
        self.canvas.delete("all")
        x = (canvas_width - self.photo.width()) // 2
        y = (canvas_height - self.photo.height()) // 2
        self.canvas.create_image(x, y, anchor=tk.NW, image=self.photo)
        
        # 更新信息标签
        if metadata:
            info_text = self.format_metadata(metadata)
            self.info_label.config(text=info_text)
    
    def format_metadata(self, metadata):
        """格式化元数据信息"""
        lines = []
        if isinstance(metadata, dict):
            frame_id = metadata.get('frame_sequence', 'N/A')
            lines.append(f"Frame: {frame_id}")
            
            pose = metadata.get('pose', {})
            if pose:
                pos = pose.get('position', [])
                if len(pos) >= 3:
                    lines.append(f"Pos: ({pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f})")
            
            roi = metadata.get('roi', {})
            if roi:
                label = roi.get('label', 'N/A')
                confidence = roi.get('confidence', 0)
                lines.append(f"Label: {label} Conf: {confidence:.2f}")
        
        return "\n".join(lines) if lines else "等待数据..."
    
    def update_status(self, connected=False):
        """更新连接状态"""
        self.stats['tcp_connected'] = connected
        if connected:
            self.status_label.config(foreground='green')
            self.status_text.config(text="已连接")
        else:
            self.status_label.config(foreground='red')
            self.status_text.config(text="未连接")
    
    def export_to_csv(self):
        """导出CSV文件"""
        if not self.summary_data:
            self.update_final_result("没有数据可导出")
            return
        
        filename = filedialog.asksaveasfilename(
            title="导出CSV文件",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        
        if filename:
            try:
                with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
                    # 写入汇总数据
                    writer = csv.DictWriter(f, fieldnames=self.summary_columns)
                    writer.writeheader()
                    for idx, (key, data) in enumerate(sorted(self.summary_data.items()), 1):
                        writer.writerow({
                            '序号': idx,
                            '商品信息': data['商品信息'],
                            '识数量': data['识数量'],
                            '库存数量': data['库存数量'],
                            '批次': data['批次'],
                            '货架': data['货架']
                        })
                self.update_final_result(f"已导出到: {filename}")
            except Exception as e:
                self.update_final_result(f"导出失败: {e}")
    
    def import_from_csv(self):
        """从CSV文件导入到最终识别结果区域"""
        filename = filedialog.askopenfilename(
            title="导入CSV文件",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        
        if filename:
            try:
                # 清空现有汇总数据
                self.summary_data.clear()
                
                with open(filename, 'r', encoding='utf-8-sig') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        # 获取商品信息作为key
                        product_info = row.get('商品信息', '')
                        if product_info:
                            # 如果序号存在，使用序号作为key的一部分
                            seq = row.get('序号', '')
                            key = f"{seq}_{product_info}" if seq else product_info
                            
                            self.summary_data[key] = {
                                '商品信息': product_info,
                                '识数量': int(row.get('识数量', 0)) if row.get('识数量', '').strip() else 0,
                                '库存数量': row.get('库存数量', '未找到库存信息'),
                                '批次': row.get('批次', ''),
                                '货架': row.get('货架', '')
                            }
                
                # 更新表格显示
                self.update_summary_table()
                self.update_final_result(f"已从CSV导入 {len(self.summary_data)} 条记录")
            except Exception as e:
                self.update_final_result(f"导入失败: {e}")
    
    def on_window_configure(self, event):
        """窗口大小变化时调整图片区域为正方形（延迟处理避免频繁调整）"""
        if event.widget != self.root:
            return  # 只处理根窗口的事件
        
        # 延迟调整，避免频繁计算
        if hasattr(self, '_adjust_scheduled'):
            self.root.after_cancel(self._adjust_scheduled)
        self._adjust_scheduled = self.root.after(50, self.adjust_image_size)
    
    def adjust_image_size(self):
        """调整图片区域为正方形"""
        try:
            # 确保窗口已完全初始化
            self.root.update_idletasks()
            
            # 获取上部分PanedWindow的高度
            top_paned_height = self.top_paned.winfo_height()
            if top_paned_height <= 0:
                return  # 高度无效，跳过
            
            # 图片区域应该是正方形（考虑padding和边框）
            padding = 10  # LabelFrame的padding（上下各5，左右各5）
            border = 10   # 考虑边框和标题栏
            image_size = top_paned_height - padding - border
            
            # 确保图片区域至少有一定宽度
            min_size = 300
            if image_size < min_size:
                image_size = min_size
            
            # 调整分割位置使图片区域为正方形
            # sashpos是分割条的位置，也就是图片区域的宽度
            current_sash_pos = self.top_paned.sashpos(0)
            if current_sash_pos is None or current_sash_pos < min_size:
                current_sash_pos = image_size
            
            # 如果当前分割位置与目标大小差异较大，才调整（避免频繁调整）
            if abs(current_sash_pos - image_size) > 10:
                self.top_paned.sashpos(0, image_size)
                
                # 同时调整下部分分割位置，使统计信息区域与图片等宽
                if hasattr(self, 'bottom_paned'):
                    current_bottom_sash = self.bottom_paned.sashpos(0)
                    if current_bottom_sash is None or abs(current_bottom_sash - image_size) > 10:
                        self.bottom_paned.sashpos(0, image_size)
        except Exception as e:
            # 忽略调整错误（可能是窗口还未完全初始化）
            pass
    
    def _load_config(self, config_file):
        """加载配置文件"""
        try:
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    config = json.load(f)
                    print(f"✅ 已加载配置文件: {config_file}")
                    return config
            else:
                print(f"⚠️ 配置文件不存在: {config_file}，使用默认配置")
                return {}
        except Exception as e:
            print(f"❌ 加载配置文件失败: {e}，使用默认配置")
            return {}
    
    def _init_nng_server(self):
        """初始化NNG服务器（监听模式）"""
        try:
            self.nng_subscriber = pynng.Sub0()
            self.nng_subscriber.recv_timeout = 3000
            self.nng_subscriber.subscribe(b"")
            if self.listen_host == '0.0.0.0':
                listen_addr = f"tcp://*:{self.listen_port}"
            else:
                listen_addr = f"tcp://{self.listen_host}:{self.listen_port}"
            self.nng_subscriber.listen(listen_addr)
            print(f"✅ NNG服务器启动，监听: {self.listen_host}:{self.listen_port}")
        except Exception as e:
            print(f"❌ NNG服务器启动失败: {e}")
            raise
    
    def _init_ack_sender(self):
        """初始化ACK发送器"""
        try:
            self.ack_sender = pynng.Pub0()
            ack_addr = f"tcp://{self.camera_node_ip}:{self.ack_port}"
            self.ack_sender.dial(ack_addr, block=False)
            print(f"✅ ACK发送器已连接: {ack_addr}")
        except Exception as e:
            print(f"⚠️ ACK发送器初始化失败: {e}")
            self.ack_sender = None
    
    def _send_ack(self, frame_sequence, timestamp_ms):
        """发送ACK消息"""
        if self.ack_sender:
            try:
                ack_data = (
                    frame_sequence.to_bytes(2, byteorder='big') +
                    timestamp_ms.to_bytes(4, byteorder='big')
                )
                self.ack_sender.send(ack_data)
            except Exception as e:
                pass  # 静默处理ACK发送失败
    
    def _init_dbr(self):
        """初始化多线程DBR识别"""
        try:
            err_code, err_str = LicenseManager.init_license("t0083YQEAAIxyZ63FS23f0lbnGqIWVNzyJUhlk6dSuGADrJOsEZqnYvegAZSqltDyy/PWWuBX508E6/Ib4GVkVU2PMdf4fVuY/r2pvDcjy6TyBN1USaY=")
            if err_code != EnumErrorCode.EC_OK and err_code != EnumErrorCode.EC_LICENSE_WARNING:
                print(f"❌ DBR 许可证初始化失败: {err_code} - {err_str}")
                self.dbr_enabled = False
                return
            
            self.dbr_queue = queue.Queue(maxsize=200)
            print(f"✅ 多线程DBR已启用：{self.dbr_thread_count}个线程，超时时间：{self.dbr_timeout}ms")
            
            # 准备日志文件
            try:
                log_dir = os.path.join(os.path.dirname(__file__), 'test_results')
                os.makedirs(log_dir, exist_ok=True)
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                self.dbr_log_file = os.path.join(log_dir, f'dbr_multithread_result_{ts}.log')
                with open(self.dbr_log_file, 'a', encoding='utf-8') as f:
                    f.write('# global_seq, recv_seq, worker_id, slot_status, position, format, text\n')
                print(f"📝 多线程DBR结果将写入: {self.dbr_log_file}")
            except Exception as e:
                print(f"⚠️ DBR日志初始化失败: {e}")
                self.dbr_log_file = None
        except Exception as e:
            print(f"❌ DBR初始化异常: {e}")
            self.dbr_enabled = False
    
    def _deserialize_crops(self, serialized_data):
        """反序列化裁剪数据"""
        crops = []
        ptr = 0
        
        # 解析帧头
        if len(serialized_data) >= 6:
            frame_sequence = int.from_bytes(serialized_data[0:2], byteorder='big')
            timestamp_ms = int.from_bytes(serialized_data[2:6], byteorder='big')
            ptr = 6
            self.current_frame_sequence = frame_sequence
            if self.ack_sender:
                self._send_ack(frame_sequence, timestamp_ms)
        else:
            ptr = 0
        
        while ptr < len(serialized_data):
            # 读取元数据
            metadata_length = int.from_bytes(serialized_data[ptr:ptr+4], byteorder='big')
            ptr += 4
            metadata_bytes = serialized_data[ptr:ptr+metadata_length]
            ptr += metadata_length
            metadata = json.loads(metadata_bytes.decode('utf-8'))
            
            # 读取图像数据
            img_length = int.from_bytes(serialized_data[ptr:ptr+4], byteorder='big')
            ptr += 4
            img_data = serialized_data[ptr:ptr+img_length]
            ptr += img_length
            
            crops.append({
                'metadata': metadata,
                'image_data': img_data
            })
        
        return crops
    
    def setup_mouse_callback(self):
        """设置鼠标回调函数"""
        def mouse_callback(event, x, y, flags, param):
            if event == cv2.EVENT_LBUTTONDOWN:
                self.handle_mouse_click(x, y)
        self.mouse_callback = mouse_callback
    
    def handle_mouse_click(self, x, y):
        """处理鼠标点击事件"""
        if self.read_index != self.latest_index:
            return
        if self.left_arrow_rect and self.is_point_in_rect(x, y, self.left_arrow_rect):
            N = min(1000, self.received_count)
            if self.delta > (1 - N):
                self.delta -= 1
        elif self.right_arrow_rect and self.is_point_in_rect(x, y, self.right_arrow_rect):
            if self.delta < 0:
                self.delta += 1
    
    def is_point_in_rect(self, x, y, rect):
        """检查点是否在矩形区域内"""
        if rect is None:
            return False
        x1, y1, x2, y2 = rect
        return x1 <= x <= x2 and y1 <= y <= y2
    
    def nng_receive_loop(self):
        """NNG接收数据循环"""
        while self.running:
            try:
                serialized_data = self.nng_subscriber.recv()
                crops_data = self._deserialize_crops(serialized_data)
                self._check_frame_loss()
                
                self.received_count += len(crops_data)
                self.last_successful_receive = time.time()
                self.stats['tcp_connected'] = True
                self.root.after(0, lambda: self.update_status(True))
                
                for crop in crops_data:
                    self.recv_seq_counter += 1
                    recv_seq = self.recv_seq_counter
                    slot = {
                        'metadata': crop.get('metadata'),
                        'image_data': crop.get('image_data'),
                        'recv_seq': recv_seq,
                        'slot_index': self.write_index,
                        'frame_sequence': self.current_frame_sequence,
                        'dbr_elapsed_ms': None,
                        'dbr_items': None,
                    }
                    self.crops_buffer[self.write_index] = slot
                    self.write_index = (self.write_index + 1) % self.slot_num
                    
                    if self.dbr_enabled and self.dbr_queue is not None:
                        jpeg_bytes = slot.get('image_data')
                        if isinstance(jpeg_bytes, (bytes, bytearray)):
                            slot_index = (self.write_index - 1) % self.slot_num
                            payload = (recv_seq, jpeg_bytes, slot_index)
                            try:
                                self.dbr_queue.put_nowait(payload)
                            except queue.Full:
                                self.dbr_dropped_frames += 1
                                try:
                                    _ = self.dbr_queue.get_nowait()
                                    self.dbr_queue.put_nowait(payload)
                                except:
                                    pass
                
                self.latest_index = (self.write_index - 1) % self.slot_num
                
            except pynng.Timeout:
                continue
            except nng_exceptions.Closed:
                print("🔒 NNG Socket已关闭")
                break
            except Exception as e:
                print(f"❌ NNG接收异常: {e}")
                time.sleep(0.1)
    
    def opencv_display_loop(self):
        """GUI图片显示循环（集成到Tkinter Canvas）"""
        while self.running:
            try:
                if self.read_index != self.latest_index:
                    self.delta = 0
                    self.locked_delta = 0
                    
                    if self.first_crop:
                        self.locked_latest_index = self.latest_index
                        photos_to_show = (self.locked_latest_index - self.read_index) % self.slot_num
                        if photos_to_show == 0:
                            photos_to_show = 1
                        display_duration = self.base_round_duration / photos_to_show
                    
                    current_time = time.time()
                    if self.first_crop:
                        self.read_index = (self.read_index + 1) % self.slot_num
                        self.last_switch_time = current_time
                    else:
                        if current_time - self.last_switch_time >= display_duration:
                            self.read_index = (self.read_index + 1) % self.slot_num
                            self.last_switch_time = current_time
                        else:
                            time.sleep(0.001)
                            continue
                    
                    if self.read_index == self.locked_latest_index:
                        self.first_crop = True
                    else:
                        self.first_crop = False
                else:
                    # 没有新照片时，检查delta变化
                    if self.delta != self.locked_delta:
                        self.locked_delta = self.delta
                        # 更新GUI中的图片显示
                        self.root.after(0, self.update_image_display)
                    
                    time.sleep(0.01)
                    continue
                
                # 有新照片时，更新GUI中的图片显示
                self.root.after(0, self.update_image_display)
                
                # 检查delta变化
                if self.delta != self.locked_delta:
                    self.locked_delta = self.delta
                    self.root.after(0, self.update_image_display)
                    
            except Exception as e:
                print(f"显示循环错误: {e}")
                time.sleep(0.01)
    
    def start_update_threads(self):
        """启动更新线程"""
        threading.Thread(target=self.opencv_display_loop, daemon=True).start()
        threading.Thread(target=self.nng_receive_loop, daemon=True).start()
        threading.Thread(target=self.log_file_monitor_loop, daemon=True).start()
        if self.dbr_enabled:
            self._start_dbr_workers()
        self.root.after(100, self.ui_update_loop)
    
    def image_update_loop(self):
        """图像更新循环"""
        while self.running:
            try:
                image, metadata = self.image_queue.get(timeout=0.5)
                self.root.after(0, lambda img=image, meta=metadata: self.update_image(img, meta))
                self.stats['total_images'] += 1
                self.stats['last_update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            except queue.Empty:
                continue
            except Exception as e:
                print(f"图像更新错误: {e}")
    
    def log_file_monitor_loop(self):
        """日志文件监听循环"""
        while self.running:
            if self.auto_find_latest_var.get():
                latest_log = self.find_latest_log_file()
                if latest_log and latest_log != self.log_file_path:
                    self.log_file_path = latest_log
                    self.log_path_var.set(latest_log)
                    self.last_log_position = 0
                    self.root.after(0, lambda: self.update_final_result(f"自动切换到最新日志: {os.path.basename(latest_log)}"))
            
            if self.auto_refresh_var.get() and self.log_file_path:
                try:
                    if os.path.exists(self.log_file_path):
                        with open(self.log_file_path, 'r', encoding='utf-8') as f:
                            f.seek(self.last_log_position)
                            new_lines = f.readlines()
                            self.last_log_position = f.tell()
                            
                            for line in new_lines:
                                line = line.strip()
                                if line and not line.startswith('#'):
                                    self.root.after(0, lambda l=line: self.parse_and_add_result(l))
                                    self.root.after(0, self.update_statistics)
                                    self.root.after(0, self.update_summary_table)
                except Exception as e:
                    print(f"日志文件监听错误: {e}")
            
            time.sleep(1)
    
    def ui_update_loop(self):
        """UI更新循环"""
        if self.running:
            self.root.after(100, self.ui_update_loop)
    
    def add_image_data(self, image, metadata=None):
        """从外部添加图像数据"""
        self.image_queue.put((image, metadata))
    
    def _start_dbr_workers(self):
        """启动多个DBR工作线程"""
        print(f"🚀 启动 {self.dbr_thread_count} 个DBR工作线程...")
        for i in range(self.dbr_thread_count):
            thread = threading.Thread(
                target=self.dbr_worker_loop,
                args=(i,),
                daemon=True,
                name=f"DBR-Worker-{i}"
            )
            thread.start()
            self.dbr_threads.append(thread)
        print(f"✅ {self.dbr_thread_count} 个DBR工作线程已启动")
    
    def dbr_worker_loop(self, worker_id):
        """多线程DBR识别工作线程"""
        print(f"🔍 DBR工作线程{worker_id}已启动")
        try:
            cvr_instance = CaptureVisionRouter()
        except Exception as e:
            print(f"❌ DBR工作线程初始化失败: {e}")
            return
        
        while self.running and self.dbr_enabled and self.dbr_queue is not None:
            try:
                payload = self.dbr_queue.get(timeout=0.2)
            except:
                continue
            
            try:
                recv_seq, jpeg_bytes, slot_index = payload
                
                t0 = time.time()
                captured_result = cvr_instance.capture(jpeg_bytes, EnumPresetTemplate.PT_READ_BARCODES)
                elapsed_ms = (time.time() - t0) * 1000.0
                
                if elapsed_ms > self.dbr_timeout:
                    continue
                
                with self.dbr_stats_lock:
                    self.dbr_total_time_ms += elapsed_ms
                    self.dbr_total_attempts += 1
                
                if captured_result.get_error_code() != EnumErrorCode.EC_OK and \
                   captured_result.get_error_code() != EnumErrorCode.EC_UNSUPPORTED_JSON_KEY_WARNING:
                    continue
                
                barcode_result = captured_result.get_decoded_barcodes_result()
                if barcode_result is None or barcode_result.get_items() == 0:
                    continue
                
                items = barcode_result.get_items()
                
                with self.dbr_stats_lock:
                    self.dbr_total_decoded += len(items)
                
                # 写入日志文件
                if recv_seq is not None and self.dbr_log_file:
                    try:
                        result_items = []
                        for it in items:
                            try:
                                result_items.append({
                                    'fmt': it.get_format_string(),
                                    'text': it.get_text(),
                                    'confidence': getattr(it, 'get_confidence', lambda: None)()
                                })
                            except:
                                result_items.append({'fmt': '<unk>', 'text': '<unk>', 'confidence': None})
                        
                        slot_status = "N/A"
                        position_str = "NA"
                        if slot_index is not None:
                            try:
                                slot = self.crops_buffer[slot_index]
                                if slot and isinstance(slot, dict) and slot.get('recv_seq') == recv_seq:
                                    slot_status = str(slot_index)
                                    metadata = slot.get('metadata') or {}
                                    pose_info = metadata.get('pose', {})
                                    position_array = pose_info.get('position', [0.0, 0.0, 0.0])
                                    if len(position_array) >= 3:
                                        px = f"{position_array[0]:.2f}"
                                        py = f"{position_array[1]:.2f}"
                                        pz = f"{position_array[2]:.2f}"
                                        position_str = f"({px},{py},{pz})"
                            except:
                                pass
                        
                        with self.dbr_stats_lock:
                            with open(self.dbr_log_file, 'a', encoding='utf-8') as f:
                                for it in result_items:
                                    self.dbr_global_seq += 1
                                    fmt = it.get('fmt', 'UNK')
                                    txt = it.get('text', '')
                                    f.write(f"{self.dbr_global_seq},{recv_seq},{worker_id},{slot_status},{position_str},{fmt},{txt}\n")
                        
                        # 更新GUI表格（通过日志文件监听）
                        
                    except Exception as e:
                        print(f"⚠️ DBR日志写入失败: {e}")
                
                # 回写到槽位
                if recv_seq is not None and slot_index is not None:
                    try:
                        slot = self.crops_buffer[slot_index]
                        if slot and isinstance(slot, dict) and slot.get('recv_seq') == recv_seq:
                            result_items = []
                            for it in items:
                                try:
                                    result_items.append({
                                        'fmt': it.get_format_string(),
                                        'text': it.get_text(),
                                        'confidence': getattr(it, 'get_confidence', lambda: None)()
                                    })
                                except:
                                    result_items.append({'fmt': '<unk>', 'text': '<unk>', 'confidence': None})
                            slot['dbr_elapsed_ms'] = float(f"{elapsed_ms:.1f}")
                            slot['dbr_items'] = result_items
                    except:
                        pass
            
            except Exception as e:
                print(f"❌ DBR识别异常: {e}")
    
    def _check_frame_loss(self):
        """检测丢帧"""
        if not hasattr(self, 'current_frame_sequence'):
            return
        
        current_seq = self.current_frame_sequence
        if not hasattr(self, 'last_frame_sequence'):
            self.last_frame_sequence = current_seq
            return
        
        if current_seq > self.last_frame_sequence:
            lost_count = current_seq - self.last_frame_sequence - 1
            if lost_count > 0:
                print(f"⚠️ 检测到丢帧: 从 {self.last_frame_sequence} 到 {current_seq}, 丢帧数 {lost_count}")
        elif current_seq < self.last_frame_sequence:
            print(f"🔄 序号回退: 从 {self.last_frame_sequence} 到 {current_seq}")
        
        self.last_frame_sequence = current_seq
    
    def manual_dbr_trigger(self):
        """手动触发DBR识别"""
        if not self.dbr_enabled or self.dbr_queue is None:
            print("❌ 多线程DBR未启用")
            return
        
        display_index = (self.read_index + self.locked_delta) % self.slot_num
        current_crop = self.crops_buffer[display_index]
        
        if not current_crop or not isinstance(current_crop, dict):
            return
        
        img_data = current_crop.get('image_data')
        if not isinstance(img_data, (bytes, bytearray)):
            return
        
        try:
            self.recv_seq_counter += 1
            manual_recv_seq = self.recv_seq_counter
            payload = (manual_recv_seq, img_data, display_index)
            self.dbr_queue.put(payload)
        except Exception as e:
            print(f"❌ 手动识别异常: {e}")
    
    def on_closing(self):
        """关闭窗口时的处理"""
        self.running = False
        # 关闭OpenCV窗口
        try:
            cv2.destroyAllWindows()
        except:
            pass
        # 关闭NNG连接
        if self.nng_subscriber:
            try:
                self.nng_subscriber.close()
            except:
                pass
        self.root.destroy()


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='二维码识别GUI - 集成版')
    parser.add_argument('--host', help='监听IP地址 (优先级最高，覆盖配置文件)')
    parser.add_argument('--client', help='相机节点IP地址 (优先级最高，覆盖配置文件)')
    parser.add_argument('--dbr', action='store_true', help='启用内置DBR识别')
    
    args = parser.parse_args()
    
    root = tk.Tk()
    app = QRViewerGUI(root, listen_host=args.host, camera_ip=args.client, enable_dbr=args.dbr)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    
    if app.auto_find_latest_var.get():
        latest_log = app.find_latest_log_file()
        if latest_log:
            app.load_log_file(latest_log)
    
    root.mainloop()


if __name__ == '__main__':
    main()

