#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""使用本地图片/视频文件发送数据到接收器"""
import cv2
import pynng
import json
import time
import sys
import os
from turbojpeg import TurboJPEG

if len(sys.argv) < 2:
    print("用法: python3 send_file.py <图片或视频文件路径> [--fps 10] [--host 192.168.0.104]")
    sys.exit(1)

file_path = sys.argv[1]
if not os.path.exists(file_path):
    print(f"❌ 文件不存在: {file_path}")
    sys.exit(1)

# 解析FPS参数
fps = 10
if '--fps' in sys.argv:
    idx = sys.argv.index('--fps')
    if idx + 1 < len(sys.argv):
        fps = int(sys.argv[idx + 1])

# 解析HOST参数
host = "localhost"
if '--host' in sys.argv:
    idx = sys.argv.index('--host')
    if idx + 1 < len(sys.argv):
        host = sys.argv[idx + 1]

# 解析PORT参数
port = 6666  # 默认端口
if '--port' in sys.argv:
    idx = sys.argv.index('--port')
    if idx + 1 < len(sys.argv):
        port = int(sys.argv[idx + 1])

jpeg = TurboJPEG()
pub = pynng.Pub0()
pub.dial(f"tcp://{host}:{port}", block=True)
print(f"✅ 已连接到接收器 {host}:{port}，发送文件: {file_path}")

frame_seq = 0

# 判断是视频还是图片
is_video = file_path.lower().endswith(('.mp4', '.avi', '.mov', '.mkv', '.flv'))

if is_video:
    cap = cv2.VideoCapture(file_path)
    if not cap.isOpened():
        print(f"❌ 无法打开视频文件")
        sys.exit(1)
    print(f"📹 视频模式，播放帧率: {fps} fps")
    interval = 1.0 / fps
    last_time = time.time()
    
    while True:
        ret, frame = cap.read()
        if not ret:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue
        
        # 控制帧率
        now = time.time()
        if now - last_time < interval:
            time.sleep(interval - (now - last_time))
        last_time = time.time()
        
        # 序列化
        jpeg_bytes = jpeg.encode(frame)
        h, w = frame.shape[:2]
        meta = {
            'roi': {'x':0, 'y':0, 'width':w, 'height':h, 'label':'frame', 'confidence':1.0},
            'camera': {'id':0},
            'pose': {'position':[0,0,0]},
            'yaw_deg':0.0
        }
        meta_bytes = json.dumps(meta).encode('utf-8')
        
        frame_seq += 1
        timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFF  # 确保4字节范围
        data = (frame_seq.to_bytes(2,'big') + 
                timestamp_ms.to_bytes(4,'big') +
                len(meta_bytes).to_bytes(4,'big') + meta_bytes +
                len(jpeg_bytes).to_bytes(4,'big') + jpeg_bytes)
        pub.send(data)
else:
    # 图片模式
    image = cv2.imread(file_path)
    if image is None:
        print(f"❌ 无法读取图片")
        sys.exit(1)
    print(f"📷 图片模式，持续发送，帧率: {fps} fps，按Ctrl+C退出")
    interval = 1.0 / fps
    last_time = time.time()
    
    while True:
        # 控制帧率
        now = time.time()
        if now - last_time < interval:
            time.sleep(interval - (now - last_time))
        last_time = time.time()
        
        # 序列化
        jpeg_bytes = jpeg.encode(image)
        h, w = image.shape[:2]
        meta = {
            'roi': {'x':0, 'y':0, 'width':w, 'height':h, 'label':'frame', 'confidence':1.0},
            'camera': {'id':0},
            'pose': {'position':[0,0,0]},
            'yaw_deg':0.0
        }
        meta_bytes = json.dumps(meta).encode('utf-8')
        
        frame_seq += 1
        timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFF  # 确保4字节范围
        data = (frame_seq.to_bytes(2,'big') + 
                timestamp_ms.to_bytes(4,'big') +
                len(meta_bytes).to_bytes(4,'big') + meta_bytes +
                len(jpeg_bytes).to_bytes(4,'big') + jpeg_bytes)
        pub.send(data)

