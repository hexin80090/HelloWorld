#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
二维码识别上位机界面程序
用于展示 simple_receiver.py 脚本的识别结果
按照文档设计：区域1（统计）、区域2（最终识别结果）、区域3（图片）、区域4（每次识别结果）
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
from datetime import datetime
from collections import defaultdict


class QRViewerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("二维码识别结果展示系统")
        self.root.geometry("1600x1000")
        
        # 数据队列和状态
        self.image_queue = queue.Queue()
        self.current_image = None
        self.current_metadata = None
        self.running = True
        
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
        """创建区域3：图片显示面板"""
        # Canvas用于显示图像
        self.canvas = tk.Canvas(parent, bg='black', relief=tk.SUNKEN, borderwidth=2)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        # 信息显示（覆盖在图像上）
        info_frame = ttk.Frame(parent)
        info_frame.place(in_=self.canvas, x=10, y=10, anchor=tk.NW)
        
        self.info_label = ttk.Label(
            info_frame, 
            text="等待图像数据...",
            background='black',
            foreground='lime',
            font=('Courier', 9)
        )
        self.info_label.pack()
        
        # TCP连接状态指示
        self.status_frame = ttk.Frame(parent)
        self.status_frame.place(in_=self.canvas, relx=1.0, rely=0, x=-10, y=10, anchor=tk.NE)
        
        self.status_label = ttk.Label(
            self.status_frame,
            text="●",
            font=('Arial', 16),
            foreground='red'
        )
        self.status_label.pack()
        
        self.status_text = ttk.Label(
            self.status_frame,
            text="未连接",
            font=('Arial', 8)
        )
        self.status_text.pack()
    
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
    
    def start_update_threads(self):
        """启动更新线程"""
        threading.Thread(target=self.image_update_loop, daemon=True).start()
        threading.Thread(target=self.log_file_monitor_loop, daemon=True).start()
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
    
    def on_closing(self):
        """关闭窗口时的处理"""
        self.running = False
        self.root.destroy()


def main():
    root = tk.Tk()
    app = QRViewerGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    
    if app.auto_find_latest_var.get():
        latest_log = app.find_latest_log_file()
        if latest_log:
            app.load_log_file(latest_log)
    
    root.mainloop()


if __name__ == '__main__':
    main()

