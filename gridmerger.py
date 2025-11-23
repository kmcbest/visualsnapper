import os
import sys

# --- 1. High DPI 设置 (必须放在最前面，防止警告和缩放异常) ---
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
# 兼容性设置，防止控制台报错 QT_DEVICE_PIXEL_RATIO
os.environ["QT_SCALE_FACTOR"] = "1" 

import subprocess
from PyQt5 import QtWidgets, QtCore, QtGui

# --- MPV 检查与加载 ---
def ensure_mpv_dll_loaded():
    dll_names = ["mpv-1.dll", "mpv-2.dll", "libmpv-2.dll", "libmpv.dll"]
    paths_to_check = [os.path.dirname(os.path.abspath(__file__))]
    paths_to_check.extend(os.environ.get("PATH", "").split(os.pathsep))
    
    for path in paths_to_check:
        if not path or not os.path.exists(path): continue
        for dll in dll_names:
            if os.path.isfile(os.path.join(path, dll)):
                if hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(path)
                    except: pass
                os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")
                return True
    return False

ensure_mpv_dll_loaded()
try:
    import mpv
except ImportError:
    print("Critical: 'python-mpv' not installed or libmpv not found.")
    mpv = None

# --- 工具：时间格式化 ---
def ms_to_fmt(ms):
    if ms is None: return "00:00:00"
    seconds = int(ms / 1000)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def fmt_to_ms(fmt):
    try:
        parts = fmt.split(':')
        if len(parts) == 3:
            h, m, s = map(int, parts)
            return (h * 3600 + m * 60 + s) * 1000
    except: pass
    return 0

# --- 核心修复：自定义视频渲染控件 ---
# 重写 sizeHint 是防止窗口被撑大的终极方案
class VideoRenderWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: black;")
        # 忽略策略：不管内容多大，都服从布局
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)
    
    def sizeHint(self):
        # 关键：告诉布局管理器，我的理想大小是 0x0
        # 这样布局管理器就会根据剩余空间拉伸我，而不是被我撑大
        return QtCore.QSize(0, 0)

# --- 核心控件：双向范围滑块 ---
class RangeSlider(QtWidgets.QWidget):
    rangeChanged = QtCore.pyqtSignal(float, float)
    seekRequest = QtCore.pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.start_ratio = 0.0
        self.end_ratio = 1.0
        self.playback_ratio = 0.0
        self.dragging_handle = None
        self.margin = 10

    def set_range(self, start, end):
        self.start_ratio = max(0.0, min(1.0, start))
        self.end_ratio = max(0.0, min(1.0, end))
        self.update()

    def set_playback_pos(self, ratio):
        self.playback_ratio = ratio
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        w = self.width() - 2 * self.margin
        h = self.height()
        y_center = h // 2
        x_start = self.margin
        
        # 轨道
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(60, 60, 60))
        painter.drawRoundedRect(x_start, y_center - 2, w, 4, 2, 2)

        # 选中区
        px_s = x_start + self.start_ratio * w
        px_e = x_start + self.end_ratio * w
        painter.setBrush(QtGui.QColor(0, 120, 215))
        painter.drawRoundedRect(int(px_s), int(y_center - 3), int(px_e - px_s), 6, 2, 2)

        # 播放头
        px_play = x_start + self.playback_ratio * w
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 2))
        painter.drawLine(int(px_play), y_center - 8, int(px_play), y_center + 8)

        # 手柄
        self.draw_handle(painter, px_s, y_center, self.dragging_handle == 'start')
        self.draw_handle(painter, px_e, y_center, self.dragging_handle == 'end')

    def draw_handle(self, painter, x, y, active):
        color = QtGui.QColor(255, 255, 255) if active else QtGui.QColor(200, 200, 200)
        painter.setBrush(color)
        painter.setPen(QtCore.Qt.NoPen)
        radius = 8 if active else 6
        painter.drawEllipse(QtCore.QPoint(int(x), int(y)), radius, radius)

    def mousePressEvent(self, event):
        x = event.x()
        w = self.width() - 2 * self.margin
        x_start = self.margin
        click_ratio = (x - x_start) / w if w > 0 else 0
        
        px_s = x_start + self.start_ratio * w
        px_e = x_start + self.end_ratio * w
        
        threshold = 15
        if abs(x - px_s) < threshold: self.dragging_handle = 'start'
        elif abs(x - px_e) < threshold: self.dragging_handle = 'end'
        else:
            self.seekRequest.emit(min(1.0, max(0.0, click_ratio)))
            return
        self.update()

    def mouseMoveEvent(self, event):
        if not self.dragging_handle: return
        w = self.width() - 2 * self.margin
        ratio = max(0.0, min(1.0, (event.x() - self.margin) / w))
        
        if self.dragging_handle == 'start':
            self.start_ratio = min(ratio, self.end_ratio)
        elif self.dragging_handle == 'end':
            self.end_ratio = max(ratio, self.start_ratio)
            
        self.rangeChanged.emit(self.start_ratio, self.end_ratio)
        self.update()

    def mouseReleaseEvent(self, event):
        self.dragging_handle = None
        self.update()

# --- 组件：单个视频槽位 ---
class VideoSlot(QtWidgets.QFrame):
    def __init__(self, index):
        super().__init__()
        self.index = index
        self.file_path = None
        self.duration = 0
        self.setup_ui()
        
        if mpv:
            # 关键：使用自定义的 Widget 的 winId
            self.player = mpv.MPV(wid=str(int(self.video_surface.winId())), ytdl=False, osc=False)
            self.player.observe_property('time-pos', self.on_time_update)
            self.player.observe_property('duration', self.on_duration_update)
        else:
            self.player = None

    def setup_ui(self):
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setStyleSheet("QFrame { background-color: #222; border-radius: 5px; } QLabel { color: #ccc; }")
        
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(5,5,5,5)
        layout.setSpacing(5)

        # 1. 视频区域 (使用自定义类 VideoRenderWidget)
        self.video_surface = VideoRenderWidget()
        layout.addWidget(self.video_surface)

        # 2. 范围滑块
        self.range_slider = RangeSlider()
        self.range_slider.rangeChanged.connect(self.on_range_changed)
        self.range_slider.seekRequest.connect(self.on_seek_request)
        layout.addWidget(self.range_slider)

        # 3. 时间输入与打开按钮
        time_layout = QtWidgets.QHBoxLayout()
        self.btn_open = QtWidgets.QPushButton(f"📂 视频 {self.index+1}")
        self.btn_open.setMaximumWidth(250)
        
        self.btn_open.setStyleSheet("""
            QPushButton { 
                background-color: #444; 
                color: white; 
                text-align: left; 
                padding-left: 10px; 
                border: 1px solid #555;
            }
        """)
        
        self.btn_open.clicked.connect(self.open_file)
        
        self.le_start = QtWidgets.QLineEdit("00:00:00")
        self.le_start.setFixedWidth(70)
        self.le_start.editingFinished.connect(self.on_text_changed)
        
        self.le_end = QtWidgets.QLineEdit("00:00:00")
        self.le_end.setFixedWidth(70)
        self.le_end.editingFinished.connect(self.on_text_changed)
        
        time_layout.addWidget(self.btn_open)
        time_layout.addStretch()
        time_layout.addWidget(QtWidgets.QLabel("始:"))
        time_layout.addWidget(self.le_start)
        time_layout.addWidget(QtWidgets.QLabel("终:"))
        time_layout.addWidget(self.le_end)
        layout.addLayout(time_layout)

        # 4. 控制按钮
        ctrl_layout = QtWidgets.QHBoxLayout()
        btn_style = "QPushButton { background-color: #333; color: white; padding: 3px; }"
        
        def mk_btn(text, func, style=None):
            b = QtWidgets.QPushButton(text)
            b.setStyleSheet(style if style else btn_style)
            b.clicked.connect(func)
            return b

        ctrl_layout.addWidget(mk_btn("⏮ -60s", lambda: self.seek(-60)))
        ctrl_layout.addWidget(mk_btn("⏪ -5s", lambda: self.seek(-5)))
        self.btn_play = mk_btn("▶/⏸", self.toggle_play, "background-color: #2e7d32; color: white; font-weight: bold;")
        ctrl_layout.addWidget(self.btn_play)
        ctrl_layout.addWidget(mk_btn("+5s ⏩", lambda: self.seek(5)))
        ctrl_layout.addWidget(mk_btn("+60s ⏭", lambda: self.seek(60)))
        layout.addLayout(ctrl_layout)

    def open_file(self):
        fname, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择视频", "", "Video (*.mp4 *.mkv *.avi *.mov *.ts)")
        if fname:
            self.file_path = fname
            self.player.play(fname)
            self.player.pause = False
            self.range_slider.set_range(0.0, 1.0)
            self.btn_open.setText(f"🎞️ {os.path.basename(fname)}")
            self.btn_open.setToolTip(fname)

    def on_duration_update(self, _name, val):
        if val:
            self.duration = val
            if self.le_end.text() == "00:00:00":
                self.le_end.setText(ms_to_fmt(val * 1000))

    def on_time_update(self, _name, val):
        if val and self.duration > 0:
            self.range_slider.set_playback_pos(val / self.duration)

    def toggle_play(self):
        if self.player: self.player.pause = not self.player.pause

    def seek(self, offset):
        if self.player: self.player.seek(offset, reference="relative")

    def on_seek_request(self, ratio):
        if self.player and self.duration > 0:
            self.player.seek(ratio * self.duration, reference="absolute")

    def on_range_changed(self, start_r, end_r):
        if self.duration > 0:
            self.le_start.setText(ms_to_fmt(start_r * self.duration * 1000))
            self.le_end.setText(ms_to_fmt(end_r * self.duration * 1000))

    def on_text_changed(self):
        if self.duration <= 0: return
        s = fmt_to_ms(self.le_start.text())
        e = fmt_to_ms(self.le_end.text())
        self.range_slider.set_range(s / (self.duration * 1000), e / (self.duration * 1000))

    def get_cut_info(self):
        if not self.file_path: return None
        return {"file": self.file_path, "start": self.le_start.text(), "end": self.le_end.text()}

# --- FFmpeg 生成线程 (实时输出版) ---
class GeneratorWorker(QtCore.QThread):
    finished = QtCore.pyqtSignal(bool, str)
    
    def __init__(self, slots_data):
        super().__init__()
        self.data = slots_data

    def run(self):
        valid_inputs = [d for d in self.data if d is not None]
        if len(valid_inputs) < 4:
            self.finished.emit(False, "必须加载 4 个视频文件才能生成。")
            return

        output_file = "output_2x2_result.mp4"
        cmd = ["ffmpeg", "-y", "-hide_banner"]
        
        for i, item in enumerate(valid_inputs):
            cmd.extend(["-ss", item['start'], "-to", item['end'], "-i", item['file']])

        fc = (
            "nullsrc=size=1920x1080[base];"
            "[0:v]setpts=PTS-STARTPTS,scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2[v0];"
            "[1:v]setpts=PTS-STARTPTS,scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2[v1];"
            "[2:v]setpts=PTS-STARTPTS,scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2[v2];"
            "[3:v]setpts=PTS-STARTPTS,scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2[v3];"
            "[base][v0]overlay=shortest=1[t1];"
            "[t1][v1]overlay=shortest=1:x=960[t2];"
            "[t2][v2]overlay=shortest=1:y=540[t3];"
            "[t3][v3]overlay=shortest=1:x=960:y=540[outv]"
        )
        
        cmd.extend([
            "-filter_complex", fc, 
            "-map", "[outv]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-stats",
            output_file
        ])

        print(">>> 正在启动 FFmpeg，命令如下:")
        # print(" ".join(cmd))
        print(">>> 请留意下方实时输出...")

        try:
            # 关键：使用 Popen 和 PIPE 来实时捕获输出
            # stderr=subprocess.STDOUT 将错误流（FFmpeg的进度信息通常在这里）合并到标准输出
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT,
                universal_newlines=True, # 文本模式
                encoding='utf-8',
                errors='replace'
            )

            # 实时读取并打印
            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                if line:
                    print(line.strip()) # 实时打印到 PyCharm/终端 控制台

            return_code = process.poll()
            
            if return_code == 0:
                self.finished.emit(True, f"生成成功！\n文件保存为: {os.path.abspath(output_file)}")
            else:
                self.finished.emit(False, "FFmpeg 执行失败，请检查控制台输出的错误信息。")

        except Exception as e:
            self.finished.emit(False, f"执行异常: {str(e)}")

# --- 主窗口 ---
class GridMerger(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("2x2 视频分频合成器")
        self.resize(1400, 900)
        self.slots = []
        self.init_ui()

    def init_ui(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #1a1a1a; color: white; }
            QLineEdit { background-color: #333; color: white; border: 1px solid #555; padding: 2px; }
            QMessageBox { background-color: #333; color: white; }
        """)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        
        main_layout = QtWidgets.QVBoxLayout(central)
        
        # 2x2 网格区域
        grid_layout = QtWidgets.QGridLayout()
        grid_layout.setSpacing(10)
        
        # 关键：设置行和列的拉伸比例为 1:1，强制均分
        grid_layout.setRowStretch(0, 1)
        grid_layout.setRowStretch(1, 1)
        grid_layout.setColumnStretch(0, 1)
        grid_layout.setColumnStretch(1, 1)
        
        for i in range(4):
            slot = VideoSlot(i)
            self.slots.append(slot)
            row, col = divmod(i, 2)
            grid_layout.addWidget(slot, row, col)
            
        main_layout.addLayout(grid_layout, stretch=1)

        # 底部
        bottom_layout = QtWidgets.QHBoxLayout()
        self.status_lbl = QtWidgets.QLabel("准备就绪。请加载 4 个视频。")
        self.status_lbl.setStyleSheet("color: #aaa; font-size: 14px;")
        
        self.btn_generate = QtWidgets.QPushButton("🎬 生成合并视频")
        self.btn_generate.setMinimumHeight(50)
        self.btn_generate.setStyleSheet("QPushButton { background-color: #d32f2f; color: white; font-weight: bold; border-radius: 5px; }")
        self.btn_generate.clicked.connect(self.start_generation)

        bottom_layout.addWidget(self.status_lbl)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.btn_generate)
        main_layout.addLayout(bottom_layout)

    def start_generation(self):
        data = []
        for i, slot in enumerate(self.slots):
            info = slot.get_cut_info()
            if not info:
                QtWidgets.QMessageBox.warning(self, "提示", f"槽位 {i+1} 为空。")
                return
            data.append(info)
        
        self.btn_generate.setEnabled(False)
        self.btn_generate.setText("⏳ 生成中... (查看控制台进度)")
        
        self.worker = GeneratorWorker(data)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def on_finished(self, success, msg):
        self.btn_generate.setEnabled(True)
        self.btn_generate.setText("🎬 生成合并视频")
        self.status_lbl.setText("完成。" if success else "失败。")
        QtWidgets.QMessageBox.information(self, "结果", msg) if success else QtWidgets.QMessageBox.critical(self, "错误", msg)

if __name__ == "__main__":
    # 确保在创建 App 之前设置 High DPI 属性
    if hasattr(QtCore.Qt, 'AA_EnableHighDpiScaling'):
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    if hasattr(QtCore.Qt, 'AA_UseHighDpiPixmaps'):
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    win = GridMerger()
    win.show()
    sys.exit(app.exec_())