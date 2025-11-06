#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pynng
import pynng.exceptions as nng_exceptions
import json
import numpy as np
import cv2
import time
import threading
import socket
import os
from datetime import datetime
from turbojpeg import TurboJPEG
from dynamsoft_barcode_reader_bundle import *

class SimpleQRReceiver:
    def __init__(self, listen_host=None, camera_ip=None, enable_dbr=False):
        # 自动加载配置文件（类似ROS launch文件）
        # 配置文件位于camera_capture/config目录下
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'camera_config.json')
        self.config = self._load_config(config_path)
        
        # 端口写死
        self.listen_port = 5555  # 写死数据端口
        self.ack_port = 5556  # 写死ACK端口
        
        # listen_host 优先级：命令行参数 > 配置文件 > 默认值
        if listen_host:  # 命令行参数优先级最高
            self.listen_host = listen_host
        elif 'listen_host' in self.config:  # 配置文件次之
            self.listen_host = self.config['listen_host']
        else:  # 默认值最低
            self.listen_host = '0.0.0.0'
        
        # camera_node_ip 优先级：命令行参数 > 配置文件 > 默认值
        if camera_ip:  # 命令行参数优先级最高
            self.camera_node_ip = camera_ip
        elif 'camera_node_ip' in self.config:  # 配置文件次之
            self.camera_node_ip = self.config['camera_node_ip']
        else:  # 默认值最低
            self.camera_node_ip = '192.168.0.176'
        
        self.subscriber = None
        
        # 统计信息
        self.received_count = 0
        self.total_bytes = 0
        self.start_time = time.time()
        self.last_receive_time = None
        self.total_runtime = 0  # 总运行时间（秒）
        
        # 帧间隔时间统计
        self.frame_intervals = []  # 存储每帧的间隔时间
        self.last_frame_time = None  # 上一帧的接收时间
        self.stats_interval = 30.0  # 统计间隔（秒）
        
        # 丢帧统计
        self.lost_frames_count = 0  # 累计丢帧数
        self.last_frame_sequence = 0  # 上一个接收到的帧序号
        
        # 显示相关
        self.current_image = None
        self.current_metadata = None
        self.display_thread = None
        self.running = False
        self.cleanup_done = False  # 清理标志，防止重复清理
        
        # 循环队列显示控制
        self.slot_num = 200  # 槽位数量配置（集中管理）
        self.crops_buffer = [None] * self.slot_num  # 固定大小的循环队列
        self.write_index = 0  # 写入位置
        self.read_index = -1  # 读取位置 (-1表示还没有开始读取)
        self.latest_index = -1  # 最新照片位置（通知display用）
        self.locked_latest_index = -1  # 锁定的最新位置
        self.first_crop = True  # 是否是第一张照片
        self.base_round_duration = 0.033  # 33ms，适配30fps视频流（每帧33.3ms）
        self.last_switch_time = 0  # 上次切换时间
        self.recv_seq_counter = 0  # 接收序号（单调递增）
        
        # 手动浏览控制
        self.delta = 0  # 浏览偏移量，0表示最新照片，负值表示往前翻
        self.locked_delta = 0  # 锁定的delta值，用于显示时保持稳定
        
        # 鼠标点击区域
        self.left_arrow_rect = None
        self.right_arrow_rect = None
        
        # 连通性测试相关
        self.tcp_connected = False
        self.last_successful_receive = 0  # 初始化为0，表示还没有成功接收过数据
        self.health_check_thread = None
        
        # ACK发送器（用于延迟监控）
        self.ack_sender = None
        
        # 初始化TurboJPEG（自动探测 + 回退）
        try:
            self.jpeg = TurboJPEG()  # 优先用默认查找
        except Exception:
            # Windows环境回退：使用你的实际安装路径
            self.jpeg = TurboJPEG(r"C:\libjpeg-turbo64\bin\libturbojpeg.dll")

        # DBR 相关
        self.dbr_enabled = bool(enable_dbr)
        # 从配置文件读取MaxParallelTasks，默认为8
        self.dbr_thread_count = self.config.get('MaxParallelTasks', 8)
        # 从配置文件读取Timeout，默认为10000ms
        self.dbr_timeout = self.config.get('Timeout', 10000)
        self.dbr_queue = None
        self.dbr_threads = []  # 存储所有DBR线程
        self.dbr_last_report = time.time()
        self.dbr_total_decoded = 0
        self.dbr_last_fixed_report = time.time()
        self.dbr_log_file = None
        self.dbr_global_seq = 0  # 全局序列号，从1开始递增
        self.dbr_dropped_frames = 0  # DBR队列丢弃帧计数
        self.dbr_start_time = time.time()  # DBR开始时间，用于计算平均识别速度
        self.dbr_total_time_ms = 0.0  # DBR累计识别时间（毫秒）
        self.dbr_total_attempts = 0  # DBR总尝试次数（包括成功和失败）
        
        # 多线程DBR统计锁
        self.dbr_stats_lock = threading.Lock()
        
        # 启动NNG服务器
        try:
            self.subscriber = pynng.Sub0()
            self.subscriber.recv_timeout = 3000
            self.subscriber.subscribe(b"")
            # Windows兼容的地址格式
            if self.listen_host == '0.0.0.0':
                listen_addr = f"tcp://*:{self.listen_port}"
            else:
                listen_addr = f"tcp://{self.listen_host}:{self.listen_port}"
            self.subscriber.listen(listen_addr)
            print(f"✅ 服务器启动，监听: {self.listen_host}:{self.listen_port}")
        except Exception as e:
            print(f"❌ 服务器启动失败: {e}")
            raise
        
        print(f"相机节点: {self.camera_node_ip}:{self.ack_port}")
        
        # 设置鼠标回调函数
        self.setup_mouse_callback()
        
        # 初始化ACK发送器
        self._init_ack_sender()

        # 初始化 DBR（按需）
        if self.dbr_enabled:
            self._init_dbr()

    def _init_dbr(self):
        """初始化多线程 DBR 识别（直接接受 JPEG bytes）"""
        try:
            err_code, err_str = LicenseManager.init_license("t0083YQEAAIxyZ63FS23f0lbnGqIWVNzyJUhlk6dSuGADrJOsEZqnYvegAZSqltDyy/PWWuBX508E6/Ib4GVkVU2PMdf4fVuY/r2pvDcjy6TyBN1USaY=")
            if err_code != EnumErrorCode.EC_OK and err_code != EnumErrorCode.EC_LICENSE_WARNING:
                print(f"❌ DBR 许可证初始化失败: {err_code} - {err_str}")
                self.dbr_enabled = False
                return
            
            # 创建共享任务队列
            self.dbr_queue = __import__('queue').Queue(maxsize=200)  # 增大队列容量
            print(f"✅ 多线程DBR 已启用：{self.dbr_thread_count}个线程，超时时间：{self.dbr_timeout}ms，将直接用 JPEG 字节识别")
            
            # 准备结果日志文件
            try:
                log_dir = os.path.join(os.path.dirname(__file__), 'test_results')
                os.makedirs(log_dir, exist_ok=True)
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                self.dbr_log_file = os.path.join(log_dir, f'dbr_multithread_result_{ts}.log')
                with open(self.dbr_log_file, 'a', encoding='utf-8') as f:
                    f.write('# 全局序号, 接收序号, 工作线程ID, 槽位状态, 位置坐标, 格式, 文本内容\n')
                print(f"📝 多线程DBR结果将写入: {self.dbr_log_file}")
            except Exception as e:
                print(f"⚠️ DBR日志初始化失败: {e}")
                self.dbr_log_file = None
        except Exception as e:
            print(f"❌ DBR 初始化异常: {e}")
            self.dbr_enabled = False
    
    def _load_config(self, config_file):
        """加载配置文件"""
        import json
        import os
        
        try:
            # 尝试从当前目录加载
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
    
    def setup_mouse_callback(self):
        """设置鼠标回调函数"""
        def mouse_callback(event, x, y, flags, param):
            if event == cv2.EVENT_LBUTTONDOWN:  # 左键点击
                self.handle_mouse_click(x, y)
        
        # 在创建窗口后设置回调
        self.mouse_callback = mouse_callback
    
    def handle_mouse_click(self, x, y):
        """处理鼠标点击事件"""
        # 只有在没有新照片时才能翻滚
        if self.read_index != self.latest_index:
            return
        
        # 检查是否点击在左箭头区域
        if self.left_arrow_rect and self.is_point_in_rect(x, y, self.left_arrow_rect):
            N = min(1000, self.received_count)
            if self.delta > (1 - N):  # 只有没到最前面时才能往前翻
                self.delta -= 1
        
        # 检查是否点击在右箭头区域
        elif self.right_arrow_rect and self.is_point_in_rect(x, y, self.right_arrow_rect):
            if self.delta < 0:  # 只有delta < 0时才能往后翻
                self.delta += 1
    
    def is_point_in_rect(self, x, y, rect):
        """检查点是否在矩形区域内"""
        if rect is None:
            return False
        x1, y1, x2, y2 = rect
        return x1 <= x <= x2 and y1 <= y <= y2
    
    def _init_ack_sender(self):
        """初始化ACK发送器"""
        try:
            import pynng
            self.ack_sender = pynng.Pub0()
            # 发送到camera_node的ACK端口
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
                # ACK消息：2字节序列号 + 4字节发送时间戳（用于延迟计算）
                ack_data = (
                    frame_sequence.to_bytes(2, byteorder='big') +
                    timestamp_ms.to_bytes(4, byteorder='big')
                )
                self.ack_sender.send(ack_data)
            except Exception as e:
                print(f"❌ 发送ACK失败: {e}")
        
    def start(self):
        """启动接收器"""
        try:
            
            # 1. 启动接收线程
            self.running = True
            self.receive_thread = threading.Thread(target=self.receive_data_loop, daemon=True)
            self.receive_thread.start()
            
            # 2. 启动显示线程
            self.display_thread = threading.Thread(target=self.display_loop, daemon=True)
            self.display_thread.start()
            
            # 3. 启动统计线程
            self.stats_thread = threading.Thread(target=self.stats_loop, daemon=True)
            self.stats_thread.start()
            
            # 5. 启动多线程DBR识别（可选）
            if self.dbr_enabled and self.dbr_queue is not None and len(self.dbr_threads) == 0:
                self._start_dbr_workers()

            # 6. 启动TCP健康检查线程
            self.health_check_thread = threading.Thread(target=self.tcp_health_check_loop, daemon=True)
            self.health_check_thread.start()
            
            print("接收器已启动，按Ctrl+C退出")
            
            # 5. 主循环
            try:
                while self.running:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n正在退出...")
                self.running = False
            except Exception as e:
                print(f"主循环异常: {e}")
                self.running = False
                
        except Exception as e:
            print(f"启动失败: {e}")
        finally:
            self.cleanup()
    
    
    def is_tcp_connected(self):
        """测试TCP连接状态（服务器端检查是否有客户端连接）"""
        try:
            # 作为服务器，检查是否有客户端连接
            # 通过检查是否有数据接收来判断连接状态
            if self.last_successful_receive > 0:
                # 如果最近30秒内有数据接收，认为连接正常
                return (time.time() - self.last_successful_receive) < 30
            else:
                # 如果还没有接收过数据，认为未连接
                return False
        except Exception as e:
            print(f"TCP连接状态检查异常: {e}")
            return False
    
    def tcp_health_check_loop(self):
        """TCP连接健康检查循环"""
        print("TCP健康检查线程启动")
        while self.running:
            try:
                # 测试TCP连接
                old_status = self.tcp_connected
                self.tcp_connected = self.is_tcp_connected()
                
                # 状态变化时打印信息
                if old_status != self.tcp_connected:
                    if self.tcp_connected:
                        print(f"✅ 客户端已连接: {self.listen_host}:{self.listen_port}")
                    else:
                        print(f"❌ 客户端未连接: {self.listen_host}:{self.listen_port}")
                
                # 每5秒检查一次
                time.sleep(5)
                
            except Exception as e:
                print(f"TCP健康检查错误: {e}")
                time.sleep(5)
        
        print("TCP健康检查线程已停止")
    
    def _start_dbr_workers(self):
        """启动多个DBR工作线程"""
        print(f"🚀 启动 {self.dbr_thread_count} 个DBR工作线程...")
        for i in range(self.dbr_thread_count):
            thread = threading.Thread(
                target=self.dbr_worker_loop, 
                args=(i,),  # 传递线程ID
                daemon=True,
                name=f"DBR-Worker-{i}"
            )
            thread.start()
            self.dbr_threads.append(thread)
        print(f"✅ {self.dbr_thread_count} 个DBR工作线程已启动")
    
    def receive_data_loop(self):
        """接收数据循环"""
        while self.running:
            try:
                # 直接尝试接收数据
                serialized_data = self.subscriber.recv()
                self.total_bytes += len(serialized_data)
                
                # 反序列化
                crops_data = self.deserialize_crops(serialized_data)
                
                # 检测丢帧
                self._check_frame_loss()
                
                # 更新统计
                self.received_count += len(crops_data)
                self.last_receive_time = time.time()
                self.last_successful_receive = time.time()  # 更新成功接收时间
                
                # 计算帧间隔时间
                current_time = time.time()
                if self.last_frame_time is not None:
                    interval = current_time - self.last_frame_time
                    self.frame_intervals.append(interval)
                    # 只保留最近1000个间隔，避免内存过多占用
                    if len(self.frame_intervals) > 1000:
                        self.frame_intervals.pop(0)
                self.last_frame_time = current_time
                
                
                # 将所有裁剪区域添加到队列
                if crops_data:
                    # 快速写入所有照片到循环队列
                    for crop in crops_data:
                        # 生成接收序号
                        self.recv_seq_counter += 1
                        recv_seq = self.recv_seq_counter

                        # 合并到环形槽位：带上识别占位字段
                        slot = {
                            'metadata': crop.get('metadata'),
                            'image_data': crop.get('image_data'),
                            'recv_seq': recv_seq,
                            'slot_index': self.write_index,  # 记录实际的槽位索引
                            'frame_sequence': getattr(self, 'current_frame_sequence', 0),  # 添加Frame ID
                            'dbr_elapsed_ms': None,
                            'dbr_items': None,
                        }
                        self.crops_buffer[self.write_index] = slot
                        self.write_index = (self.write_index + 1) % self.slot_num

                        # 将 JPEG 直接送入 DBR 队列（可选），携带 recv_seq 和 slot_index 便于回写
                        if self.dbr_enabled and self.dbr_queue is not None:
                            jpeg_bytes = slot.get('image_data')
                            if isinstance(jpeg_bytes, (bytes, bytearray)):
                                slot_index = (self.write_index - 1) % self.slot_num  # 记录当前槽位索引（已写入的槽位）
                                payload = (recv_seq, jpeg_bytes, slot_index)
                                try:
                                    self.dbr_queue.put_nowait(payload)
                                except __import__('queue').Full:
                                    # 丢弃最旧的一条以避免堆积
                                    self.dbr_dropped_frames += 1  # 增加丢弃帧计数
                                    print(f"⚠️ DBR队列已满(100/100)，丢弃最旧数据，recv_seq={recv_seq}，累计丢弃:{self.dbr_dropped_frames}")
                                    try:
                                        _ = self.dbr_queue.get_nowait()
                                    except Exception:
                                        pass
                                    try:
                                        self.dbr_queue.put_nowait(payload)
                                        print(f"✅ DBR队列已重新加入新数据，recv_seq={recv_seq}")
                                    except Exception:
                                        print(f"❌ DBR队列重新加入失败，recv_seq={recv_seq}")
                    
                    # 一次性通知display_loop
                    self.latest_index = (self.write_index - 1) % self.slot_num
                    
                    print(f"添加 {len(crops_data)} 张新照片，写入位置: {self.write_index}，最新位置: {self.latest_index}")
                
                print(f"接收到 {len(crops_data)} 个裁剪区域，累计: {self.received_count}")
                
            except pynng.Timeout:
                # 超时，继续等待
                continue
            except nng_exceptions.Closed:
                print("🔒 Socket 已关闭，接收线程退出")
                break
            except Exception as e:
                print(f"❌ 接收线程异常: {e}")
                break

    def dbr_worker_loop(self, worker_id):
        """多线程DBR识别工作线程：每个线程独立的CaptureVisionRouter实例"""
        print(f"🔍 DBR工作线程{worker_id}已启动")
        
        # 每个线程创建独立的DBR实例
        try:
            cvr_instance = CaptureVisionRouter()
        except Exception as e:
            print(f"❌ DBR工作线程初始化失败: {e}")
            return
        
        while self.running and self.dbr_enabled and self.dbr_queue is not None:
            try:
                payload = self.dbr_queue.get(timeout=0.2)
            except Exception:
                continue

            try:
                # 统一使用 (recv_seq, jpeg_bytes, slot_index)
                recv_seq, jpeg_bytes, slot_index = payload

                t0 = time.time()
                # 使用配置的超时时间进行识别
                captured_result = cvr_instance.capture(jpeg_bytes, EnumPresetTemplate.PT_READ_BARCODES)
                elapsed_ms = (time.time() - t0) * 1000.0
                
                # 检查是否超时
                if elapsed_ms > self.dbr_timeout:
                    print(f"⚠️ DBR识别超时: {elapsed_ms:.1f}ms > {self.dbr_timeout}ms")
                    continue
                
                # 线程安全地更新统计信息
                with self.dbr_stats_lock:
                    self.dbr_total_time_ms += elapsed_ms
                    self.dbr_total_attempts += 1

                if captured_result.get_error_code() != EnumErrorCode.EC_OK and \
                   captured_result.get_error_code() != EnumErrorCode.EC_UNSUPPORTED_JSON_KEY_WARNING:
                    print(f"❌ 识别错误: {captured_result.get_error_code()} - {captured_result.get_error_string()}")
                    continue

                barcode_result = captured_result.get_decoded_barcodes_result()
                if barcode_result is None or barcode_result.get_items() == 0:
                    # 静默未识别以减少噪音
                    continue

                items = barcode_result.get_items()
                
                # 线程安全地更新解码计数
                with self.dbr_stats_lock:
                    self.dbr_total_decoded += len(items)
                
                # 打印摘要
                for idx, item in enumerate(items):
                    try:
                        fmt = item.get_format_string()
                        txt = item.get_text()
                        print(f"✅ DBR {elapsed_ms:.1f} ms | {fmt} | {txt}")
                    except Exception:
                        print(f"✅ DBR {elapsed_ms:.1f} ms | <item>")

                # 直接存储到日志文件，不依赖slot
                if recv_seq is not None and self.dbr_log_file:
                    try:
                        # 构造精简结果
                        result_items = []
                        for it in items:
                            try:
                                result_items.append({
                                    'fmt': it.get_format_string(),
                                    'text': it.get_text(),
                                    'confidence': getattr(it, 'get_confidence', lambda: None)()
                                })
                            except Exception:
                                result_items.append({'fmt': '<unk>', 'text': '<unk>', 'confidence': None})
                        
                        # 检查slot状态并获取位置信息
                        slot_status = "N/A"
                        position_str = "NA"
                        if slot_index is not None:
                            try:
                                slot = self.crops_buffer[slot_index]
                                if slot and isinstance(slot, dict) and slot.get('recv_seq') == recv_seq:
                                    slot_status = str(slot_index)  # 记录slot编号
                                    metadata = slot.get('metadata') or {}
                                    pose_info = metadata.get('pose', {})
                                    position_array = pose_info.get('position', [0.0, 0.0, 0.0])
                                    if len(position_array) >= 3:
                                        px = f"{position_array[0]:.2f}"
                                        py = f"{position_array[1]:.2f}"
                                        pz = f"{position_array[2]:.2f}"
                                        position_str = f"({px},{py},{pz})"
                            except Exception:
                                pass
                        
                        # 线程安全地写入日志文件
                        with self.dbr_stats_lock:
                            with open(self.dbr_log_file, 'a', encoding='utf-8') as f:
                                for it in result_items:
                                    self.dbr_global_seq += 1  # 全局序列号递增
                                    fmt = it.get('fmt', 'UNK')
                                    txt = it.get('text', '')
                                    f.write(f"{self.dbr_global_seq},{recv_seq},{worker_id},{slot_status},{position_str},{fmt},{txt}\n")
                        
                        print(f"✅ 存储: 全局序列号={self.dbr_global_seq}, recv_seq={recv_seq}, 识别到{len(result_items)}个结果")
                        
                    except Exception as e:
                        print(f"⚠️ DBR日志写入失败: {e}")

                # 回写到环形槽位（用于显示，可选）
                if recv_seq is not None and slot_index is not None:
                    # 尝试回写到slot（用于显示，失败也没关系）
                    try:
                        slot = self.crops_buffer[slot_index]
                        if slot and isinstance(slot, dict) and slot.get('recv_seq') == recv_seq:
                            # 构造精简结果
                            result_items = []
                            for it in items:
                                try:
                                    result_items.append({
                                        'fmt': it.get_format_string(),
                                        'text': it.get_text(),
                                        'confidence': getattr(it, 'get_confidence', lambda: None)()
                                    })
                                except Exception:
                                    result_items.append({'fmt': '<unk>', 'text': '<unk>', 'confidence': None})
                            slot['dbr_elapsed_ms'] = float(f"{elapsed_ms:.1f}")
                            slot['dbr_items'] = result_items
                    except Exception:
                        pass  # 静默处理，不打印警告

            except Exception as e:
                print(f"❌ DBR识别异常: {e}")
        
        # 静默退出，避免在程序关闭时打印
        pass
    
    def deserialize_crops(self, serialized_data):
        """反序列化裁剪数据"""
        crops = []
        ptr = 0
        
        # 解析帧头（6字节：2字节序列号 + 4字节时间戳）
        if len(serialized_data) >= 6:
            frame_sequence = int.from_bytes(serialized_data[0:2], byteorder='big')
            timestamp_ms = int.from_bytes(serialized_data[2:6], byteorder='big')
            ptr = 6
            
            # 存储当前帧序号用于丢帧检测和显示
            self.current_frame_sequence = frame_sequence
            
            # 发送ACK（如果ACK发送器可用）
            if self.ack_sender:
                self._send_ack(frame_sequence, timestamp_ms)
        else:
            ptr = 0
        
        while ptr < len(serialized_data):
            # 读取元数据长度
            metadata_length = int.from_bytes(serialized_data[ptr:ptr+4], byteorder='big')
            ptr += 4
            
            # 读取元数据
            metadata_bytes = serialized_data[ptr:ptr+metadata_length]
            ptr += metadata_length
            metadata = json.loads(metadata_bytes.decode('utf-8'))
            
            # 读取图像数据长度
            img_length = int.from_bytes(serialized_data[ptr:ptr+4], byteorder='big')
            ptr += 4
            
            # 读取图像数据
            img_data = serialized_data[ptr:ptr+img_length]
            ptr += img_length
            
            crops.append({
                'metadata': metadata,
                'image_data': img_data
            })
        
        return crops
    
    def _check_frame_loss(self):
        """检测丢帧"""
        if not hasattr(self, 'current_frame_sequence'):
            return
        
        current_seq = self.current_frame_sequence
        
        # 第一次接收，初始化
        if self.last_frame_sequence == 0:
            self.last_frame_sequence = current_seq
            return
        
        # 计算丢帧数量：current - last - 1
        if current_seq > self.last_frame_sequence:
            lost_count = current_seq - self.last_frame_sequence - 1
            if lost_count > 0:
                self.lost_frames_count += lost_count
                print(f"⚠️ 检测到丢帧: 从 {self.last_frame_sequence} 到 {current_seq}, 丢帧数 {lost_count}")
        elif current_seq < self.last_frame_sequence:
            # 序号回退，可能是重连或重启
            print(f"🔄 序号回退: 从 {self.last_frame_sequence} 到 {current_seq}")
        
        # 更新上一个序号
        self.last_frame_sequence = current_seq
    
    def display_loop(self):
        """显示循环 - 可调整大小窗口，智能显示"""
        # 初始窗口大小
        WINDOW_WIDTH = 800
        WINDOW_HEIGHT = 600
        
        # 创建可调整大小的窗口
        cv2.namedWindow("QR Receiver", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("QR Receiver", WINDOW_WIDTH, WINDOW_HEIGHT)
        
        # 设置鼠标回调函数
        cv2.setMouseCallback("QR Receiver", self.mouse_callback)
        
        # 创建显示画布
        display_canvas = np.zeros((WINDOW_HEIGHT, WINDOW_WIDTH, 3), dtype=np.uint8)
        
        # 画布初始化标志
        canvas_initialized = False
        
        while self.running:
            try:
                # 检查是否有新照片需要显示
                if self.read_index != self.latest_index:
                    # 有新照片时，清零delta，回到最新照片
                    # 对于实时视频流（30fps），直接显示最新图片，避免黑屏
                    self.delta = 0
                    self.locked_delta = 0
                    
                    # 计算待显示的图片数量
                    photos_to_show = (self.latest_index - self.read_index) % self.slot_num
                    if photos_to_show == 0:
                        photos_to_show = 1
                    
                    # 实时视频流策略：直接跳转到最新有效图片，避免分片显示导致黑屏
                    # 对于30fps（33.3ms/帧），应该立即显示最新帧，而不是尝试"播放"缓冲区中的所有帧
                    current_time = time.time()
                    
                    # 直接跳到最新位置，但确保槽位有数据
                    target_idx = self.latest_index
                    # 向前查找最近的有效槽位（最多查找20个）
                    found_valid = False
                    for offset in range(0, min(20, self.slot_num)):
                        check_idx = (target_idx - offset) % self.slot_num
                        if self.crops_buffer[check_idx] is not None:
                            self.read_index = check_idx
                            self.first_crop = True
                            self.locked_latest_index = self.latest_index
                            found_valid = True
                            break
                    
                    if not found_valid:
                        # 如果找不到有效数据，保持当前显示，避免黑屏
                        time.sleep(0.001)
                        continue
                else:
                    # 没有新照片需要显示，但在现有画布上叠加TCP状态指示灯
                    # 获取当前窗口大小
                    try:
                        window_size = cv2.getWindowImageRect("QR Receiver")
                        current_width = window_size[2]  # 窗口宽度
                        current_height = window_size[3]  # 窗口高度
                    except:
                        # 如果获取失败，使用默认大小
                        current_width, current_height = WINDOW_WIDTH, WINDOW_HEIGHT
                    
                    # 检查画布尺寸是否有效
                    if current_width <= 0 or current_height <= 0:
                        print(f"⚠️ 窗口尺寸无效: {current_width}x{current_height}，跳过绘制")
                        time.sleep(0.01)
                        continue
                    
                    # 在现有画布上叠加TCP连接状态指示灯（右上角）
                    # 确保display_canvas是连续内存
                    display_canvas = np.ascontiguousarray(display_canvas)
                    
                    indicator_color = (0, 255, 0) if self.tcp_connected else (0, 0, 255)  # 绿色=连接，红色=断开
                    
                    # 尝试绘制圆圈，失败时重建画布并用文字显示
                    try:
                        cv2.circle(display_canvas, (current_width - 50, 50), 15, indicator_color, -1)  # 绘制指示灯
                    except Exception as e:
                        print(f"cv2.circle失败，重建画布: {e}")
                        print(f"Canvas shape: {display_canvas.shape}, dtype: {display_canvas.dtype}")
                        print(f"Circle pos: ({current_width - 50}, 50), color: {indicator_color}")
                        print(f"Canvas contiguous: {display_canvas.flags['C_CONTIGUOUS']}")
                        
                        # 重建画布
                        display_canvas = np.zeros((current_height, current_width, 3), dtype=np.uint8)
                        display_canvas = np.ascontiguousarray(display_canvas)
                    
                    # 添加箭头显示（只有在没有新照片时才显示）
                    N = min(self.slot_num, self.received_count)
                    show_left_arrow = self.delta > (1 - N)
                    show_right_arrow = self.delta < 0
                    
                    # 左箭头
                    if show_left_arrow:
                        arrow_text = "<"
                        font = cv2.FONT_HERSHEY_SIMPLEX
                        scale = 2
                        thickness = 3
                        arrow_size, baseline = cv2.getTextSize(arrow_text, font, scale, thickness)
                        text_w, text_h = arrow_size

                        arrow_x = 50 - text_w  # 左边缘距离左边50像素
                        arrow_y = current_height // 2  # 基线位置

                        # 绘制箭头
                        cv2.putText(display_canvas, arrow_text, (arrow_x, arrow_y), font, scale, (255, 255, 255), thickness)

                        # 点击区域 = 文字外接矩形
                        self.left_arrow_rect = (arrow_x, arrow_y - text_h, arrow_x + text_w, arrow_y + baseline)
                    else:
                        self.left_arrow_rect = None
                    
                    # 右箭头
                    if show_right_arrow:
                        arrow_text = ">"
                        font = cv2.FONT_HERSHEY_SIMPLEX
                        scale = 2
                        thickness = 3
                        arrow_size, baseline = cv2.getTextSize(arrow_text, font, scale, thickness)
                        text_w, text_h = arrow_size

                        arrow_x = current_width - 50
                        arrow_y = current_height // 2  # 基线位置

                        # 绘制箭头
                        cv2.putText(display_canvas, arrow_text, (arrow_x, arrow_y), font, scale, (255, 255, 255), thickness)

                        # 点击区域 = 文字外接矩形
                        self.right_arrow_rect = (arrow_x, arrow_y - text_h, arrow_x + text_w, arrow_y + baseline)
                    else:
                        self.right_arrow_rect = None
                    
                    cv2.imshow("QR Receiver", display_canvas)  # 维持窗口活跃
                    
                    # 键盘控制
                    key = cv2.waitKeyEx(1) & 0xFFFFFFFF
                    #if key != 0xFFFFFFFF:  # 0xFFFFFFFF表示没有按键
                    #    print(f"按键键值: {key}")
                    
                    if key == 27:  # ESC键
                        self.running = False
                        break
                    elif key == 32:  # 空格键 - 手动触发DBR识别
                        self.manual_dbr_trigger()
                    elif key == 2424832:  # 左方向键 - 往前翻
                        N = min(self.slot_num, self.received_count)
                        if self.delta > (1 - N):  # 只有没到最前面时才能往前翻
                            self.delta -= 1
                    elif key == 2555904:  # 右方向键 - 往后翻
                        if self.delta < 0:  # 只有delta < 0时才能往后翻
                            self.delta += 1
                    
                    # 检查delta是否有变化
                    if self.delta == self.locked_delta:
                        time.sleep(0.01)
                        continue
                    else:
                        # delta有变化，更新locked_delta并继续显示
                        self.locked_delta = self.delta
                
                # 获取当前要显示的照片
                display_index = (self.read_index + self.locked_delta) % self.slot_num
                current_crop = self.crops_buffer[display_index]
                # 空槽保护，万一当前照片为空，则等待1ms后继续显示
                if not current_crop:
                    time.sleep(0.001)
                    continue

                # 重构图像
                metadata = current_crop['metadata']
                roi_info = metadata.get('roi', {})
                width = roi_info.get('width', 0)
                height = roi_info.get('height', 0)
                img_data = current_crop['image_data']
                
                # 解码JPEG压缩数据,获取实际尺寸更新width和height
                bgr_image = self.jpeg.decode(img_data)
                
                # 检查decode结果是否合法
                if (bgr_image is None or 
                    not isinstance(bgr_image, np.ndarray) or 
                    bgr_image.ndim != 3 or 
                    bgr_image.shape[2] != 3 or 
                    bgr_image.dtype != np.uint8):
                    print("⚠️ 解码失败或得到的图像不合法，丢弃该帧")
                    continue
                
                # 获取图像真实尺寸
                height, width = bgr_image.shape[:2]
                
                # 获取当前窗口大小
                try:
                    window_size = cv2.getWindowImageRect("QR Receiver")
                    current_width = window_size[2]  # 窗口宽度
                    current_height = window_size[3]  # 窗口高度
                except:
                    # 如果获取失败，使用默认大小
                    current_width, current_height = WINDOW_WIDTH, WINDOW_HEIGHT
                
                # 检查窗口尺寸是否有效
                if current_width <= 0 or current_height <= 0:
                    print(f"⚠️ 窗口尺寸无效: {current_width}x{current_height}，使用默认尺寸")
                    current_width, current_height = WINDOW_WIDTH, WINDOW_HEIGHT
                
                # 重新创建画布以匹配窗口大小
                display_canvas = np.zeros((current_height, current_width, 3), dtype=np.uint8)
                
                # 计算显示位置和大小
                if width <= current_width and height <= current_height:
                    # 小图像：居中显示
                    x_offset = (current_width - width) // 2
                    y_offset = (current_height - height) // 2
                    display_canvas[y_offset:y_offset+height, x_offset:x_offset+width] = bgr_image
                    display_width, display_height = width, height
                else:
                    # 大图像：缩放适配
                    scale = min(current_width/width, current_height/height)
                    new_width = int(width * scale)
                    new_height = int(height * scale)
                    
                    # 缩放图像
                    resized_image = cv2.resize(bgr_image, (new_width, new_height))
                    
                    # 居中放置
                    x_offset = (current_width - new_width) // 2
                    y_offset = (current_height - new_height) // 2
                    display_canvas[y_offset:y_offset+new_height, x_offset:x_offset+new_width] = resized_image
                    display_width, display_height = new_width, new_height
                
                # ✅ 确保 display_canvas 是连续内存，仅需加一次（在所有图像赋值后，绘图前）
                display_canvas = np.ascontiguousarray(display_canvas)
                assert display_canvas.flags['C_CONTIGUOUS'] 
                
                # 添加TCP连接状态指示灯（右上角）
                indicator_color = (0, 255, 0) if self.tcp_connected else (0, 0, 255)  # 绿色=连接，红色=断开
                
                # 尝试绘制圆圈，失败时重建画布并用文字显示
                try:
                    cv2.circle(display_canvas, (current_width - 50, 50), 15, indicator_color, -1)  # 绘制指示灯
                except Exception as e:
                    print(f"cv2.circle失败，重建画布: {e}")
                    print(f"Canvas shape: {display_canvas.shape}, dtype: {display_canvas.dtype}")
                    print(f"Circle pos: ({current_width - 50}, 50), color: {indicator_color}")
                    print(f"Canvas contiguous: {display_canvas.flags['C_CONTIGUOUS']}")
                    
                    # 重建画布
                    display_canvas = np.zeros((current_height, current_width, 3), dtype=np.uint8)
                    display_canvas = np.ascontiguousarray(display_canvas)
                    
                    # 用文字显示状态，不再尝试circle
                    status_text = "TCP: 连接" if self.tcp_connected else "TCP: 断开"
                    cv2.putText(display_canvas, status_text, (current_width - 150, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, indicator_color, 2)
                
                # 添加信息文本（利用新的JSON结构显示更多信息）
                roi_info = metadata.get('roi', {})
                camera_info = metadata.get('camera', {})
                pose_info = metadata.get('pose', {})
                
                # 基础信息（使用ROI中的尺寸信息）
                frame_id = current_crop.get('frame_sequence', 0)  # 获取Frame ID
                info_text = f"Frame:{frame_id} | Read:{display_index} | Latest:{self.latest_index} | Total:{self.received_count} | Size: {width}x{height}"
                cv2.putText(display_canvas, info_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                
                # 检测信息（前缀加入位置 (x,y,z)）
                label = roi_info.get('label', 'unknown')
                confidence = roi_info.get('confidence', 0.0)
                # 修正：从新的JSON格式中获取位置信息
                pose_info = metadata.get('pose', {}) if isinstance(metadata, dict) else {}
                position_array = pose_info.get('position', [0.0, 0.0, 0.0])
                # 格式化位置坐标为小数点后2位
                if len(position_array) >= 3:
                    px = f"{position_array[0]:.2f}"
                    py = f"{position_array[1]:.2f}"
                    pz = f"{position_array[2]:.2f}"
                else:
                    px = py = pz = 'n/a'
                detection_text = f"({px},{py},{pz}) Label: {label} | Confidence: {confidence:.3f}"
                cv2.putText(display_canvas, detection_text, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)

                # 叠加 DBR 识别结果（若已完成） - 底部显示
                dbr_items = current_crop.get('dbr_items') if isinstance(current_crop, dict) else None
                dbr_elapsed = current_crop.get('dbr_elapsed_ms') if isinstance(current_crop, dict) else None
                if dbr_items:
                    # 计算底部起始位置
                    max_show = min(2, len(dbr_items))
                    total_lines = 1 + max_show  # 1行为耗时 + 若干结果
                    margin_bottom = 20
                    line_gap = 20
                    base_y = current_height - margin_bottom - (total_lines - 1) * line_gap
                    # 显示耗时
                    try:
                        elapsed_text = f"DBR: {float(dbr_elapsed):.1f} ms"
                    except Exception:
                        elapsed_text = "DBR: -- ms"
                    cv2.putText(display_canvas, elapsed_text, (10, base_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
                    # 显示前两条结果摘要
                    for i in range(max_show):
                        item = dbr_items[i]
                        fmt = item.get('fmt', 'UNK')
                        txt = item.get('text', '')
                        conf = item.get('confidence', None)
                        if conf is not None:
                            line = f"[{fmt}] {txt} (conf={conf})"
                        else:
                            line = f"[{fmt}] {txt}"
                        y = base_y + (i + 1) * line_gap
                        cv2.putText(display_canvas, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
                
                # 飞行信息（只显示YAW角）
                yaw = metadata.get('yaw_deg', 0.0)
                flight_text = f"Yaw: {yaw:.1f} deg"
                cv2.putText(display_canvas, flight_text, (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
                
                
                # 添加控制提示（右下角，避免与底部DBR信息重叠）
                control_text = "Control: ESC=Quit, SPACE=Manual DBR"
                font = cv2.FONT_HERSHEY_SIMPLEX
                scale = 0.5
                thickness = 1
                (text_w, text_h), baseline = cv2.getTextSize(control_text, font, scale, thickness)
                ctrl_x = max(10, current_width - text_w - 10)
                ctrl_y = max(10, current_height - 10)
                cv2.putText(display_canvas, control_text, (ctrl_x, ctrl_y), font, scale, (255, 255, 255), thickness)
                
                # 添加边框
                cv2.rectangle(display_canvas, (0, 0), (current_width-1, current_height-1), (128, 128, 128), 2)
                
                # 显示图像
                cv2.imshow("QR Receiver", display_canvas)
                    
            except Exception as e:
                print(f"显示错误: {e}")
                # 画布检查已在循环开头处理，这里只需要简单等待
                time.sleep(0.01)
    
    def stats_loop(self):
        """统计循环"""
        while self.running:
            try:
                time.sleep(self.stats_interval)  # 使用配置的统计间隔
                
                # 计算总运行时间
                self.total_runtime = time.time() - self.start_time
                
                if self.received_count > 0:
                    # 计算平均帧间隔（基于所有历史数据）
                    if len(self.frame_intervals) > 0:
                        avg_interval = sum(self.frame_intervals) / len(self.frame_intervals)
                        avg_interval_ms = avg_interval * 1000
                    else:
                        avg_interval_ms = 0
                    
                    # 计算带宽（使用总时间）
                    elapsed = time.time() - self.start_time
                    mbps = (self.total_bytes / 1024 / 1024) / elapsed if elapsed > 0 else 0
                    
                    tcp_status = "连接" if self.tcp_connected else "断开"
                    
                    # 格式化总运行时间
                    hours = int(self.total_runtime // 3600)
                    minutes = int((self.total_runtime % 3600) // 60)
                    seconds = int(self.total_runtime % 60)
                    if hours > 0:
                        runtime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                    else:
                        runtime_str = f"{minutes:02d}:{seconds:02d}"
                    
                    # 基础统计信息
                    stats_text = f"统计: 运行时间 {runtime_str}, 接收 {self.received_count} 个区域, " \
                                f"平均间隔: {avg_interval_ms:.1f} ms, 带宽: {mbps:.1f} MB/s, TCP: {tcp_status}, " \
                                f"丢帧: {self.lost_frames_count}"
                    
                    # 如果启用了DBR，添加DBR相关统计
                    if self.dbr_enabled:
                        avg_time_ms = self.dbr_total_time_ms / self.dbr_total_attempts if self.dbr_total_attempts > 0 else 0
                        stats_text += f", DBR识别: {self.dbr_total_decoded}, DBR丢弃: {self.dbr_dropped_frames}, DBR平均: {avg_time_ms:.1f} ms, 超时: {self.dbr_timeout}ms"
                    
                    print(stats_text)
                    
            except Exception as e:
                print(f"统计错误: {e}")
    
    def manual_dbr_trigger(self):
        """手动触发DBR识别当前显示的照片（使用多线程队列）"""
        if not self.dbr_enabled or self.dbr_queue is None:
            print("❌ 多线程DBR未启用，无法手动识别")
            return
        
        # 获取当前显示的照片
        display_index = (self.read_index + self.locked_delta) % self.slot_num
        current_crop = self.crops_buffer[display_index]
        
        if not current_crop or not isinstance(current_crop, dict):
            print("❌ 当前没有可识别的照片")
            return
        
        img_data = current_crop.get('image_data')
        if not isinstance(img_data, (bytes, bytearray)):
            print("❌ 当前照片数据无效")
            return
        
        try:
            print("🔍 手动触发多线程DBR识别...")
            # 生成手动触发的recv_seq
            self.recv_seq_counter += 1
            manual_recv_seq = self.recv_seq_counter
            
            # 将任务放入多线程队列
            payload = (manual_recv_seq, img_data, display_index)
            self.dbr_queue.put(payload)
            print(f"✅ 手动识别任务已加入队列，recv_seq={manual_recv_seq}，等待多线程处理...")
                    
        except Exception as e:
            print(f"❌ 手动识别异常: {e}")

    def cleanup(self):
        """清理资源"""
        # 防止重复清理
        if self.cleanup_done:
            return
        self.cleanup_done = True
        
        print("正在清理资源...")
        self.running = False
        
        # 计算最终运行时间
        final_runtime = time.time() - self.start_time
        hours = int(final_runtime // 3600)
        minutes = int((final_runtime % 3600) // 60)
        seconds = int(final_runtime % 60)
        if hours > 0:
            runtime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            runtime_str = f"{minutes:02d}:{seconds:02d}"
        
        print(f"📊 程序总运行时间: {runtime_str}")
        print(f"📊 总接收区域: {self.received_count}")
        print(f"📊 总数据量: {self.total_bytes / 1024 / 1024:.1f} MB")
        
        # 等待所有DBR线程结束
        if hasattr(self, 'dbr_threads') and self.dbr_threads:
            print("等待DBR线程结束...")
            for thread in self.dbr_threads:
                if thread.is_alive():
                    thread.join(timeout=2.0)  # 最多等待2秒
            self.dbr_threads.clear()
        
        # 关闭网络连接
        if self.subscriber:
            try:
                self.subscriber.close()
            except:
                pass
        
        # 关闭OpenCV窗口
        try:
            cv2.destroyAllWindows()
        except:
            pass
        
        print("接收器已关闭")

if __name__ == '__main__':
    import argparse
    import signal
    import sys
    
    # 全局接收器实例，用于信号处理
    receiver = None
    
    def signal_handler(signum, frame):
        """处理退出信号"""
        global receiver
        if receiver:
            print("\n收到退出信号，正在清理...")
            receiver.running = False
            receiver.cleanup()
        sys.exit(0)
    
    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # 解析命令行参数
        parser = argparse.ArgumentParser(description='Simple QR Receiver - 接收相机数据并显示')
        parser.add_argument('--host', help='监听IP地址 (优先级最高，覆盖配置文件)')
        parser.add_argument('--client', help='相机节点IP地址 (优先级最高，覆盖配置文件)')
        parser.add_argument('--dbr', action='store_true', help='启用内置DBR识别（直接喂JPEG字节，控制台输出）')
        
        args = parser.parse_args()
        
        # 创建接收器实例（自动加载配置文件）
        receiver = SimpleQRReceiver(listen_host=args.host, camera_ip=args.client, enable_dbr=args.dbr)
        receiver.start()
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    except Exception as e:
        print(f"程序异常: {e}")
    finally:
        if receiver:
            receiver.cleanup()
