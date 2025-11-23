import sys
import os
import subprocess
import configparser
import time
from PyQt5 import QtWidgets, QtCore, QtGui

# --- MPV DLL 加载逻辑 (保持不变，增强鲁棒性) ---
def ensure_mpv_dll_loaded():
    dll_names = ["mpv-1.dll", "mpv-2.dll", "libmpv-2.dll", "libmpv.dll"]
    paths_to_check = [
        os.path.dirname(os.path.abspath(__file__)),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "mpv"),  # 常见子目录
        *os.environ.get("PATH", "").split(os.pathsep)
    ]
    
    found = False
    for path in paths_to_check:
        if not path or not os.path.exists(path): continue
        for dll in dll_names:
            if os.path.isfile(os.path.join(path, dll)):
                # Python 3.8+ 需要 add_dll_directory
                if hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(path)
                    except Exception:
                        pass
                os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")
                return True
    return False

if not ensure_mpv_dll_loaded():
    print("Warning: libmpv DLL not found. Player might fail to load.")

try:
    import mpv
except OSError:
    print("Error: Could not load mpv library.")
    mpv = None

# --- 工具函数：时间转换 ---
def ms_to_timestamp(ms):
    if ms is None: return "00:00:00.000"
    seconds = ms / 1000
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"

def timestamp_to_ms(ts):
    try:
        h, m, s = ts.split(':')
        return (int(h) * 3600 + int(m) * 60 + float(s)) * 1000
    except ValueError:
        return 0

# --- 工作线程：FFmpeg 切割 ---
class FFmpegWorker(QtCore.QThread):
    progress_signal = QtCore.pyqtSignal(str) # 发送日志/进度消息
    finished_signal = QtCore.pyqtSignal(int) # 发送完成的任务数量

    def __init__(self, tasks):
        super().__init__()
        self.tasks = tasks # list of dicts

    def run(self):
        success_count = 0
        total = len(self.tasks)
        
        for idx, task in enumerate(self.tasks):
            self.progress_signal.emit(f"[{idx+1}/{total}] 处理中: {os.path.basename(task['output'])}")
            
            # 构造命令
            # 注意：-ss 在 -i 之前可以极快定位（关键帧），但在 -c copy 模式下可能会有点不准
            # 但为了速度和防卡顿，通常推荐 -ss 在前。
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", task['start_ts'],
                "-i", task['input_file'],
                "-t", task['duration'],
                "-c", "copy",
                "-map_metadata", "0", # 保留元数据
                task['output']
            ]
            
            try:
                # 使用 subprocess 调用，不弹出 CMD 窗口
                startupinfo = None
                if os.name == 'nt':
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                
                process = subprocess.run(
                    cmd, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE,
                    startupinfo=startupinfo,
                    encoding='utf-8'
                )
                
                if process.returncode == 0:
                    success_count += 1
                else:
                    self.progress_signal.emit(f"❌ 错误: {process.stderr}")
            except Exception as e:
                self.progress_signal.emit(f"❌ 执行异常: {str(e)}")
        
        self.finished_signal.emit(success_count)

# --- 组件：单个分段行 ---
# --- 组件：单个分段行 ---
class SegmentRow(QtWidgets.QWidget):
    delete_clicked = QtCore.pyqtSignal()
    jump_clicked = QtCore.pyqtSignal(float) 

    def __init__(self, start_ms, default_tags):
        super().__init__()
        self.start_ms = start_ms
        self.end_ms = None
        
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)

        # 跳转按钮
        self.btn_jump = QtWidgets.QPushButton("▶")
        self.btn_jump.setFixedWidth(25)
        self.btn_jump.setToolTip("跳转到开始时间")
        # 按钮也不能抢焦点
        self.btn_jump.setFocusPolicy(QtCore.Qt.NoFocus) 
        self.btn_jump.clicked.connect(lambda: self.jump_clicked.emit(self.start_ms / 1000.0))
        layout.addWidget(self.btn_jump)

        # 时间显示 (只读，不需要焦点)
        self.le_start = QtWidgets.QLineEdit(ms_to_timestamp(start_ms))
        self.le_start.setFixedWidth(90)
        self.le_start.setFocusPolicy(QtCore.Qt.ClickFocus) # 允许点击复制，但不抢占全局
        
        self.le_end = QtWidgets.QLineEdit("")
        self.le_end.setFixedWidth(90)
        self.le_end.setPlaceholderText("未设置")
        self.le_end.setFocusPolicy(QtCore.Qt.ClickFocus)
        
        layout.addWidget(QtWidgets.QLabel("始:"))
        layout.addWidget(self.le_start)
        layout.addWidget(QtWidgets.QLabel("终:"))
        layout.addWidget(self.le_end)

        # 时长
        self.lbl_duration = QtWidgets.QLabel("0.0s")
        self.lbl_duration.setFixedWidth(60)
        layout.addWidget(self.lbl_duration)

        # 标签 (这是抢焦点的罪魁祸首)
        self.combo_tag = QtWidgets.QComboBox()
        self.combo_tag.addItems(default_tags)
        self.combo_tag.setEditable(True)
        self.combo_tag.setFixedWidth(100)
        
        # --- 关键修改：选完后自动交还焦点给主窗口 ---
        # activated: 用户从下拉列表点了某一项
        self.combo_tag.activated.connect(self.return_focus_to_main)
        # lineEdit().returnPressed: 用户自己输入了文字按了回车
        self.combo_tag.lineEdit().returnPressed.connect(self.return_focus_to_main)
        
        layout.addWidget(self.combo_tag)

        # 删除按钮
        self.btn_del = QtWidgets.QPushButton("✖")
        self.btn_del.setFixedWidth(25)
        self.btn_del.setStyleSheet("color: red; font-weight: bold;")
        self.btn_del.setFocusPolicy(QtCore.Qt.NoFocus) # 防止抢焦点
        self.btn_del.clicked.connect(self.delete_clicked.emit)
        layout.addWidget(self.btn_del)

        # 浅色背景样式
        self.setStyleSheet("background-color: #f0f0f0; border-radius: 4px; border: 1px solid #ccc;")

    def return_focus_to_main(self):
        # self.window() 获取当前控件所在的顶层窗口（即 VisualCutterPro）
        self.window().setFocus()

    def set_end_time(self, end_ms):
        self.end_ms = end_ms
        self.le_end.setText(ms_to_timestamp(end_ms))
        self.update_duration()

    def update_duration(self):
        if self.start_ms is not None and self.end_ms is not None:
            diff = (self.end_ms - self.start_ms) / 1000.0
            self.lbl_duration.setText(f"{diff:.1f}s")
            if diff <= 0:
                self.lbl_duration.setStyleSheet("color: red")
            else:
                self.lbl_duration.setStyleSheet("color: #2e7d32") # 深绿色

    def get_data(self):
        return {
            "start_ts": self.le_start.text(),
            "end_ts": self.le_end.text(),
            "tag": self.combo_tag.currentText(),
            "valid": (self.end_ms is not None and self.end_ms > self.start_ms)
        }
        
        
# --- 自定义进度条 (点击定位) ---
class ClickableSlider(QtWidgets.QSlider):
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            val = self.minimum() + (self.maximum() - self.minimum()) * event.x() / self.width()
            self.setValue(int(val))
            event.accept()
        super().mousePressEvent(event)

# --- 主窗口 ---
class VisualCutterPro(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VisualCutter Pro - 视频无损剪辑")
        self.resize(1280, 720)
        self.current_file = None
        self.player_duration = 0
        self.is_slider_pressed = False

        # 读取配置
        self.tags = self.load_config_tags()
        
        # 初始化界面
        self.init_ui()
        
        # 初始化播放器
        if mpv:
            self.player = mpv.MPV(wid=str(int(self.video_surface.winId())), ytdl=False, osc=False)
            self.player.observe_property('time-pos', self.on_mpv_time_update)
        else:
            QtWidgets.QMessageBox.critical(self, "错误", "未检测到 MPV 库，程序无法播放视频。")
            self.player = None

    def load_config_tags(self):
        # 1. 设置默认标签
        tags = ["-default-"]
        
        # 2. 获取配置文件路径
        current_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(current_dir, "config.ini")
        
        if os.path.exists(config_path):
            try:
                # allow_no_value=True 允许只写键不写值
                cfg = configparser.ConfigParser(allow_no_value=True)
                # 关键：设置 optionxform 为 str，以保留键名的大小写（否则默认全转小写）
                cfg.optionxform = str 
                cfg.read(config_path, encoding='utf-8')
                
                if "taglist" in cfg:
                    # 获取 taglist 下的所有键
                    custom_tags = list(cfg["taglist"].keys())
                    # 将读取到的标签追加到列表中
                    tags.extend(custom_tags)
                    print(f"[Config] 读取到标签: {custom_tags}")
            except Exception as e:
                print(f"[Config] 读取出错: {e}")
        else:
            print(f"[Config] 未找到配置文件: {config_path}")
            
        return tags

    def init_ui(self):
        # 1. 删除了原本的 self.setStyleSheet(...) 以恢复浅色

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QHBoxLayout(central)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        main_layout.addWidget(splitter)

        # --- 左侧面板 ---
        left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0,0,0,0)

        # 顶部控制按钮
        btn_layout = QtWidgets.QGridLayout()
        self.btn_open = QtWidgets.QPushButton("📂 打开文件 (O)")
        self.btn_open.setShortcut("O")
        self.btn_open.clicked.connect(self.open_file)
        self.btn_open.setFocusPolicy(QtCore.Qt.NoFocus)  ### 新增：防止按钮抢焦点

        self.btn_cut = QtWidgets.QPushButton("✂️ 开始切割 (F9)")
        self.btn_cut.setShortcut("F9")
        # 去掉原本的绿色强样式，或者保留看你喜好，这里简单保留粗体
        self.btn_cut.setStyleSheet("font-weight: bold;") 
        self.btn_cut.clicked.connect(self.start_cutting)
        self.btn_cut.setFocusPolicy(QtCore.Qt.NoFocus)   ### 新增

        self.btn_clear = QtWidgets.QPushButton("🗑️ 清空列表")
        self.btn_clear.clicked.connect(self.clear_segments)
        self.btn_clear.setFocusPolicy(QtCore.Qt.NoFocus) ### 新增

        btn_layout.addWidget(self.btn_open, 0, 0)
        btn_layout.addWidget(self.btn_clear, 0, 1)
        btn_layout.addWidget(self.btn_cut, 1, 0, 1, 2)
        left_layout.addLayout(btn_layout)

        # 分段列表区域
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFocusPolicy(QtCore.Qt.NoFocus) ### 新增：防止滚动区抢上下键
        
        self.segment_container = QtWidgets.QWidget()
        self.segment_layout = QtWidgets.QVBoxLayout(self.segment_container)
        self.segment_layout.setAlignment(QtCore.Qt.AlignTop)
        self.segment_layout.setSpacing(2)
        self.scroll_area.setWidget(self.segment_container)
        left_layout.addWidget(self.scroll_area)

        # 说明标签 (颜色改回黑色/默认)
        help_lbl = QtWidgets.QLabel("快捷键:\n[S] 设起点 / [E] 设终点\n[Space] 播放/暂停\n[←/→] 微调 5s")
        help_lbl.setStyleSheet("color: #666; font-size: 11px;") # 浅色主题用深灰字
        left_layout.addWidget(help_lbl)

        splitter.addWidget(left_panel)

        # --- 右侧播放器面板 ---
        right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0,0,0,0)

        # 视频渲染窗口
        self.video_surface = QtWidgets.QWidget()
        self.video_surface.setStyleSheet("background-color: black;")
        self.video_surface.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        right_layout.addWidget(self.video_surface)

        # 进度条控制
        control_bar = QtWidgets.QHBoxLayout()
        self.lbl_curr_time = QtWidgets.QLabel("00:00:00")
        self.lbl_total_time = QtWidgets.QLabel("00:00:00")
        
        self.slider = ClickableSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setFocusPolicy(QtCore.Qt.NoFocus) ### 新增：关键！防止Slider抢左右键
        
        self.slider.sliderPressed.connect(self.on_slider_pressed)
        self.slider.sliderReleased.connect(self.on_slider_released)
        self.slider.valueChanged.connect(self.on_slider_seek)

        control_bar.addWidget(self.lbl_curr_time)
        control_bar.addWidget(self.slider)
        control_bar.addWidget(self.lbl_total_time)
        right_layout.addLayout(control_bar)
        player_btn_layout = QtWidgets.QHBoxLayout()
        player_btn_layout.setContentsMargins(0, 10, 0, 10) # 上下留点空隙
        
        # 定义通用样式
        btn_style = "height: 30px; font-weight: bold; font-size: 14px;"

        # 1. 设起点按钮
        self.btn_set_start = QtWidgets.QPushButton("⏮ 设起点 (S)")
        self.btn_set_start.setStyleSheet(btn_style)
        self.btn_set_start.setFocusPolicy(QtCore.Qt.NoFocus) # 关键：不抢焦点
        self.btn_set_start.clicked.connect(self.add_segment_start)
        
        # 2. 播放/暂停按钮
        self.btn_play_pause = QtWidgets.QPushButton("⏯ 播放/暂停 (Space)")
        self.btn_play_pause.setStyleSheet(btn_style)
        self.btn_play_pause.setFocusPolicy(QtCore.Qt.NoFocus) # 关键：不抢焦点
        self.btn_play_pause.clicked.connect(self.toggle_play_pause)

        # 3. 设终点按钮
        self.btn_set_end = QtWidgets.QPushButton("⏭ 设终点 (E)")
        self.btn_set_end.setStyleSheet(btn_style)
        self.btn_set_end.setFocusPolicy(QtCore.Qt.NoFocus) # 关键：不抢焦点
        self.btn_set_end.clicked.connect(self.set_segment_end)

        player_btn_layout.addWidget(self.btn_set_start)
        player_btn_layout.addWidget(self.btn_play_pause)
        player_btn_layout.addWidget(self.btn_set_end)
        
        right_layout.addLayout(player_btn_layout)
        
        splitter.addWidget(right_panel)
        splitter.setSizes([400, 880])

        # 状态栏
        self.status_bar = QtWidgets.QStatusBar()
        self.setStatusBar(self.status_bar)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)

    # --- 逻辑处理 ---
    def open_file(self):
        fname, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择视频", "", "Video Files (*.mp4 *.mkv *.avi *.ts *.mov)")
        if fname:
            self.current_file = fname
            self.player.play(fname)
            # 等待加载以获取元数据
            QtCore.QTimer.singleShot(500, self.update_file_info)
            self.status_bar.showMessage(f"已加载: {fname}")
            self.clear_segments()
    def toggle_play_pause(self):
            if self.player:
                self.player.pause = not self.player.pause
                
    def update_file_info(self):
        if not self.player: return
        dur = self.player.duration
        if dur:
            self.player_duration = dur
            self.lbl_total_time.setText(ms_to_timestamp(dur * 1000))

    def on_mpv_time_update(self, _name, val):
        """MPV 回调：时间更新（在子线程触发，需注意线程安全，PyQt信号槽自动处理）"""
        if val is not None and not self.is_slider_pressed:
            # 使用信号安全更新 UI
            QtCore.QMetaObject.invokeMethod(self, "update_slider_ui", QtCore.Qt.QueuedConnection, QtCore.Q_ARG(float, val))

    @QtCore.pyqtSlot(float)
    def update_slider_ui(self, val):
        self.lbl_curr_time.setText(ms_to_timestamp(val * 1000))
        if self.player_duration > 0:
            pos = (val / self.player_duration) * 1000
            self.slider.blockSignals(True)
            self.slider.setValue(int(pos))
            self.slider.blockSignals(False)

    def on_slider_pressed(self):
        self.is_slider_pressed = True

    def on_slider_released(self):
        self.is_slider_pressed = False
        # 最终跳转一次确保准确
        val = self.slider.value()
        self.on_slider_seek(val)

    def on_slider_seek(self, val):
        if self.player_duration > 0 and self.player:
            target = (val / 1000.0) * self.player_duration
            self.player.seek(target, reference="absolute")
            self.lbl_curr_time.setText(ms_to_timestamp(target * 1000))

    # --- 分段逻辑 ---
    def add_segment_start(self):
        if not self.current_file or not self.player.time_pos: return
        
        curr_ms = self.player.time_pos * 1000
        
        # 检查是否有未闭合的分段，如果有，则更新该分段的起点（用户体验优化）
        # 或者策略：始终创建新分段
        # 这里采用：如果有未闭合的，先更新它；否则创建新的
        for i in range(self.segment_layout.count()):
            w = self.segment_layout.itemAt(i).widget()
            if isinstance(w, SegmentRow) and w.end_ms is None:
                w.start_ms = curr_ms
                w.le_start.setText(ms_to_timestamp(curr_ms))
                self.status_bar.showMessage("已更新当前分段起点")
                return

        # 创建新行
        row = SegmentRow(curr_ms, self.tags)
        row.delete_clicked.connect(lambda: self.remove_segment(row))
        row.jump_clicked.connect(self.jump_to_time)
        self.segment_layout.addWidget(row)
        # 滚动到底部
        QtCore.QTimer.singleShot(100, lambda: self.scroll_area.verticalScrollBar().setValue(
            self.scroll_area.verticalScrollBar().maximum()))
        self.status_bar.showMessage("设置起点")

    def set_segment_end(self):
        if not self.current_file or not self.player.time_pos: return
        curr_ms = self.player.time_pos * 1000

        # 找最后一个未闭合的分段
        for i in range(self.segment_layout.count()):
            w = self.segment_layout.itemAt(i).widget()
            if isinstance(w, SegmentRow) and w.end_ms is None:
                w.set_end_time(curr_ms)
                self.status_bar.showMessage("设置终点")
                return
        
        self.status_bar.showMessage("没有找到未闭合的起点，请先按 S")

    def remove_segment(self, widget):
        widget.setParent(None)

    def clear_segments(self):
        while self.segment_layout.count():
            child = self.segment_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def jump_to_time(self, seconds):
        if self.player:
            self.player.seek(seconds, reference="absolute")

    # --- 切割逻辑 ---
    def start_cutting(self):
        if not self.current_file: return
        
        tasks = []
        base_dir = os.path.dirname(self.current_file)
        fname_no_ext, ext = os.path.splitext(os.path.basename(self.current_file))

        for i in range(self.segment_layout.count()):
            w = self.segment_layout.itemAt(i).widget()
            if isinstance(w, SegmentRow):
                data = w.get_data()
                if data['valid']:
                    # 格式化文件名: 原名_00-00-00_00-00-10_tag.mp4
                    safe_tag = data['tag'].replace(" ", "_").replace("/", "-")
                    start_str = data['start_ts'].replace(":", "")
                    end_str = data['end_ts'].replace(":", "")
                    out_name = f"{fname_no_ext}_{start_str}_{end_str}_{safe_tag}{ext}"
                    start_ms_val = timestamp_to_ms(data['start_ts'])
                    end_ms_val = timestamp_to_ms(data['end_ts'])
                    duration_sec = (end_ms_val - start_ms_val) / 1000.0
                    
                    if duration_sec <= 0:
                        continue # 跳过无效分段
                                        
                    tasks.append({
                        "input_file": self.current_file,
                        "start_ts": data['start_ts'],
                        "duration": str(duration_sec), # <--- 传入时长字符串
                        "output": os.path.join(base_dir, out_name)
                    })

        if not tasks:
            QtWidgets.QMessageBox.warning(self, "提示", "没有有效的完整分段（需包含起点和终点）")
            return

        # 禁用按钮防止重入
        self.btn_cut.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0) # 忙碌模式
        
        # 启动线程
        self.worker = FFmpegWorker(tasks)
        self.worker.progress_signal.connect(lambda msg: self.status_bar.showMessage(msg))
        self.worker.finished_signal.connect(self.on_cut_finished)
        self.worker.start()

    def on_cut_finished(self, count):
        self.progress_bar.setVisible(False)
        self.btn_cut.setEnabled(True)
        self.status_bar.showMessage(f"完成！成功切割 {count} 个片段。", 5000)
        QtWidgets.QMessageBox.information(self, "完成", f"处理结束。\n成功生成 {count} 个文件。")

    # --- 键盘事件 ---
    def keyPressEvent(self, event):
        if not self.player: return
        key = event.key()
        
        if key == QtCore.Qt.Key_Space:
            # self.player.pause = not self.player.pause
            self.toggle_play_pause() # 修改这里
        elif key == QtCore.Qt.Key_S:
            self.add_segment_start()
        elif key == QtCore.Qt.Key_E:
            self.set_segment_end()
        elif key == QtCore.Qt.Key_Left:
            self.player.seek(-5)
        elif key == QtCore.Qt.Key_Right:
            self.player.seek(5)
        elif key == QtCore.Qt.Key_Up:
            self.player.seek(-60)
        elif key == QtCore.Qt.Key_Down:
            self.player.seek(60)
        else:
            super().keyPressEvent(event)

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion") # 使得样式在不同平台更一致
    
    win = VisualCutterPro()
    win.show()
    sys.exit(app.exec_())