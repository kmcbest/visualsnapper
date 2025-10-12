import sys
import os
import datetime
import subprocess
import shutil
import glob
import configparser
from PyQt5 import QtWidgets, QtCore, QtGui

# --- 确保 mpv DLL 能被找到 ---
def ensure_mpv_dll_loaded(extra_dirs=None):
    dll_names = ["mpv-1.dll", "mpv-2.dll", "libmpv-2.dll", "libmpv.dll"]
    search_dirs = []
    script_dir = os.path.dirname(os.path.abspath(__file__))
    search_dirs.append(script_dir)
    if extra_dirs:
        for d in extra_dirs:
            search_dirs.append(d)
    for p in os.environ.get("PATH", "").split(os.pathsep):
        search_dirs.append(p)
    # 去重
    seen = set()
    search_dirs = [d for d in search_dirs if d and not (d in seen or seen.add(d))]
    found = False
    for d in search_dirs:
        for dll in dll_names:
            candidate = os.path.join(d, dll)
            if os.path.isfile(candidate):
                found = True
                dll_dir = d
                if hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(dll_dir)
                    except Exception:
                        pass
                os.environ["PATH"] = dll_dir + os.pathsep + os.environ.get("PATH", "")
                print(f"[mpv-dll] 加入 DLL 目录：{dll_dir}, 用文件：{dll}")
                break
        if found:
            break
    if not found:
        print("[mpv-dll] 未找到 libmpv DLL，可能会导入失败")

# 确保在 import mpv 前加载 DLL
ensure_mpv_dll_loaded(extra_dirs=None)
import mpv

class CustomSlider(QtWidgets.QSlider):
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            # 计算点击位置比例
            ratio = event.x() / self.width()
            value = self.minimum() + (self.maximum() - self.minimum()) * ratio
            self.setValue(int(value))
            event.accept()
        super().mousePressEvent(event)

class VideoCutter(QtWidgets.QMainWindow):
    flash_signal = QtCore.pyqtSignal(str)
    # --- 从 config.ini 读取默认标签 ---
    def load_default_tags(self):
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
        tags = []
        if os.path.exists(config_path):
            config = configparser.ConfigParser(allow_no_value=True, delimiters=('='))
            config.optionxform = str  # 保留大小写
            try:
                config.read(config_path, encoding='utf-8')
                if "taglist" in config:
                    # 取 [taglist] 段的每个键名作为一行
                    tags = list(config["taglist"].keys())
                    print(f"[config] 读取到标签列表: {tags}")
                else:
                    print("[config] 未找到 [taglist] 段")
            except Exception as e:
                print(f"[config] 读取配置文件出错: {e}")
        else:
            print(f"[config] 未找到配置文件: {config_path}")
        return tags

    def __init__(self):
        super().__init__()
        self.setWindowTitle("VisualCutter")
        self.setStatusBar(QtWidgets.QStatusBar())
        self.resize(1400, 800)

        # --- 主布局，左右可拉伸 ---
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)

        h_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        # --- 左侧控制区 ---
        self.control_widget = QtWidgets.QWidget()
        self.control_widget.setMinimumWidth(300)
        control_layout = QtWidgets.QVBoxLayout(self.control_widget)
        h_splitter.addWidget(self.control_widget)

        # 状态信息
        self.status_label = QtWidgets.QLabel()
        control_layout.insertWidget(0, self.status_label)

        # --- 分段计数器 ---
        self.segment_count = 0
        self.segment_count_label = QtWidgets.QLabel(f"已切分的段落: {self.segment_count}")
        self.segment_count_label.setStyleSheet("font-weight: bold;")
        control_layout.addWidget(self.segment_count_label)

        # 分段显示区域
        self.segment_scroll = QtWidgets.QScrollArea()
        self.segment_scroll.setWidgetResizable(True)
        self.segment_container = QtWidgets.QWidget()
        self.segment_layout = QtWidgets.QVBoxLayout(self.segment_container)
        self.segment_layout.setAlignment(QtCore.Qt.AlignTop)
        self.segment_container.setLayout(self.segment_layout)
        self.segment_scroll.setWidget(self.segment_container)
        control_layout.addWidget(self.segment_scroll, 1)

        # 操作按钮
        self.open_btn = QtWidgets.QPushButton("📂 打开文件")
        self.play_pause_btn = QtWidgets.QPushButton("⏯️ 播放/暂停")
        self.clear_segments_btn = QtWidgets.QPushButton("🗑️ 清空分段")
        self.cut_btn = QtWidgets.QPushButton("✂️ 切割视频")
        control_layout.addWidget(self.open_btn)
        control_layout.addWidget(self.play_pause_btn)
        control_layout.addWidget(self.clear_segments_btn)
        control_layout.addWidget(self.cut_btn)

        # --- 右侧视频区域（视频 + 进度条） ---
        self.video_container = QtWidgets.QWidget()
        video_layout = QtWidgets.QVBoxLayout(self.video_container)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.setSpacing(2)

        # 视频 widget
        self.video_widget = QtWidgets.QWidget()
        self.video_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        video_layout.addWidget(self.video_widget)

        # 进度条
        self.progress_slider = CustomSlider(QtCore.Qt.Horizontal)
        self.progress_slider.setRange(0, 1000)
        self.progress_slider.setSingleStep(1)
        self.progress_slider.setTracking(True)
        video_layout.addWidget(self.progress_slider)

        h_splitter.addWidget(self.video_container)
        h_splitter.setSizes([450, 900])

        # 添加 splitter 到 central_widget
        layout = QtWidgets.QHBoxLayout(central_widget)
        layout.addWidget(h_splitter)

        # --- mpv 播放器 ---
        self.player = mpv.MPV(
            wid=str(int(self.video_widget.winId())),
            ytdl=False,
            osc=False,
            log_handler=print,
            loglevel="info"
        )

        # --- 定时器更新进度条 ---
        self.update_timer = QtCore.QTimer()
        self.update_timer.setInterval(200)
        self.update_timer.timeout.connect(self.update_slider)
        self.update_timer.start()

        # 进度条事件
        self.progress_slider.valueChanged.connect(self.slider_seek)
        self.progress_slider.sliderPressed.connect(self.slider_press)
        self.progress_slider.sliderReleased.connect(self.slider_released)
        self.slider_is_pressed = False

        # --- 绑定按钮 ---
        self.open_btn.clicked.connect(self.open_file)
        self.play_pause_btn.clicked.connect(self.toggle_play_pause)
        self.clear_segments_btn.clicked.connect(self.clear_segments)
        self.cut_btn.clicked.connect(self.cut_video)

        self.segments = []  # 存储分段信息: (start_time, end_time, duration, tag, widget)
        self.video_file = None
        self.video_dir = None

        # 键盘事件
        self.video_widget.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.video_widget.keyPressEvent = self.keyPressEvent

        # 从 config.ini 读取默认标签
        self.default_tags = ["-notag-"] + self.load_default_tags()

    # --- 定时器更新进度条 ---
    def update_slider(self):
        if self.video_file and not self.slider_is_pressed:
            if self.player.time_pos is not None and self.player.duration is not None:
                pos = self.player.time_pos / self.player.duration * 1000
                self.progress_slider.blockSignals(True)
                self.progress_slider.setValue(int(pos))
                self.progress_slider.blockSignals(False)

    # --- 用户拖动或点击进度条 ---
    def slider_seek(self, value):
        if self.video_file and self.player.duration is not None:
            self.update_timer.stop()
            t = value / 1000.0 * self.player.duration
            print(f"[DEBUG] Seek to {t:.2f}s (slider value: {value}, duration: {self.player.duration})")
            self.player.seek(t, reference="absolute")
            QtCore.QTimer.singleShot(500, self.update_timer.start)

    def slider_press(self):
        self.slider_is_pressed = True

    def slider_released(self):
        self.slider_is_pressed = False

    # 显示消息函数
    def flash_message(self, msg, timeout=3000):
        self.statusBar().showMessage(msg, timeout)

    # --- 视频操作 ---
    def open_file(self):
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择视频文件", "", "视频文件 (*.mp4 *.mkv *.avi *.mov *.ts)"
        )
        if filename:
            # 清空现有分段
            self.clear_segments(silent=True)
            self.player.play(filename)
            self.video_file = filename
            self.video_dir = os.path.dirname(filename)
            print(f"[INFO] 打开视频: {filename}, 目录: {self.video_dir}")
            self.flash_message(f"已加载视频: {os.path.basename(filename)}")

    def toggle_play_pause(self):
        self.player.pause = not self.player.pause

    # --- 分段管理 ---
    def add_segment(self, start_time_ms=None, end_time_ms=None):
        if not self.video_file:
            QtWidgets.QMessageBox.warning(self, "提示", "请先打开视频")
            return

        # 如果没有提供开始时间，获取当前播放时间
        if start_time_ms is None:
            if self.player.time_pos is None:
                QtWidgets.QMessageBox.warning(self, "提示", "视频尚未播放")
                return
            start_time_ms = int(self.player.time_pos * 1000)

        # 检查是否有未闭合的分段
        for segment in self.segments:
            if segment[1] is None:  # 未闭合的分段
                segment[0] = start_time_ms  # 更新开始时间
                self.update_segment_widget(segment)
                self.flash_message(f"更新分段开始时间: {self.format_timestamp(start_time_ms)}")
                return

        # 新建分段
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)

        # 开始时间输入框
        start_input = QtWidgets.QLineEdit(self.format_timestamp(start_time_ms))
        start_input.setFixedWidth(100)
        layout.addWidget(start_input)

        # 结束时间输入框
        end_input = QtWidgets.QLineEdit(self.format_timestamp(end_time_ms) if end_time_ms else "")
        end_input.setFixedWidth(100)
        layout.addWidget(end_input)

        # 时长显示
        duration_label = QtWidgets.QLabel("0.000 sec")
        duration_label.setFixedWidth(80)
        layout.addWidget(duration_label)

        # Tag 下拉菜单和输入框
        tag_widget = QtWidgets.QWidget()
        tag_layout = QtWidgets.QHBoxLayout(tag_widget)
        tag_layout.setContentsMargins(0, 0, 0, 0)
        tag_combo = QtWidgets.QComboBox()
        tag_combo.addItems(self.default_tags)
        tag_combo.setCurrentIndex(0)  # 默认选中 "-notag-"
        tag_input = QtWidgets.QLineEdit()
        tag_input.setFixedWidth(100)
        tag_layout.addWidget(tag_combo)
        tag_layout.addWidget(tag_input)
        layout.addWidget(tag_widget)

        # 删除按钮
        del_btn = QtWidgets.QPushButton("❌")
        del_btn.setMaximumWidth(24)
        layout.addWidget(del_btn)

        # 绑定事件
        start_input.textChanged.connect(lambda: self.update_segment_time(widget, start_input, end_input, duration_label))
        end_input.textChanged.connect(lambda: self.update_segment_time(widget, start_input, end_input, duration_label))
        tag_combo.currentTextChanged.connect(lambda text: tag_input.setText(text))
        del_btn.clicked.connect(lambda: self.remove_segment(widget))

        # 添加到分段列表和布局
        segment = [start_time_ms, end_time_ms, None, tag_input.text(), widget]
        self.segments.append(segment)
        self.segment_layout.addWidget(widget)
        self.segment_count += 1
        self.segment_count_label.setText(f"已切分的段落: {self.segment_count}")
        self.update_segment_time(widget, start_input, end_input, duration_label)
        self.flash_message(f"添加分段: {self.format_timestamp(start_time_ms)}")

    def close_segment(self):
        if not self.video_file or self.player.time_pos is None:
            QtWidgets.QMessageBox.warning(self, "提示", "视频尚未播放")
            return
        end_time_ms = int(self.player.time_pos * 1000)
        for segment in self.segments:
            if segment[1] is None:  # 未闭合的分段
                segment[1] = end_time_ms
                self.update_segment_widget(segment)
                self.flash_message(f"闭合分段结束时间: {self.format_timestamp(end_time_ms)}")
                return
        self.flash_message("没有未闭合的分段")

    def update_segment_widget(self, segment):
        widget = segment[4]
        start_input = widget.layout().itemAt(0).widget()
        end_input = widget.layout().itemAt(1).widget()
        duration_label = widget.layout().itemAt(2).widget()
        start_input.setText(self.format_timestamp(segment[0]))
        end_input.setText(self.format_timestamp(segment[1]) if segment[1] else "")
        self.update_segment_time(widget, start_input, end_input, duration_label)

    def update_segment_time(self, widget, start_input, end_input, duration_label):
        for segment in self.segments:
            if segment[4] == widget:
                try:
                    start_ms = self.parse_timestamp(start_input.text())
                    end_ms = self.parse_timestamp(end_input.text()) if end_input.text() else None
                    segment[0] = start_ms
                    segment[1] = end_ms
                    tag_widget = widget.layout().itemAt(3).widget()
                    tag_input = tag_widget.layout().itemAt(1).widget()
                    segment[3] = tag_input.text()
                    duration = None
                    if end_ms is not None and start_ms is not None:
                        duration = (end_ms - start_ms) / 1000.0
                        segment[2] = duration
                        duration_label.setText(f"{duration:.3f} sec")
                        if duration < 0:
                            duration_label.setStyleSheet("color: red;")
                            self.flash_message("无效分段，建议删除", 5000)
                        else:
                            duration_label.setStyleSheet("")
                    else:
                        duration_label.setText("0.000 sec")
                        duration_label.setStyleSheet("")
                except ValueError:
                    duration_label.setText("Invalid")
                    duration_label.setStyleSheet("color: red;")
                break

    def remove_segment(self, widget):
        self.segments = [s for s in self.segments if s[4] != widget]
        widget.setParent(None)
        self.segment_count -= 1
        self.segment_count_label.setText(f"已切分的段落: {self.segment_count}")
        self.flash_message("已删除分段")

    def clear_segments(self, silent=False):
        if not silent:
            reply = QtWidgets.QMessageBox.question(
                self, "警告", "确定要清空所有分段吗？",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if reply != QtWidgets.QMessageBox.Yes:
                return
        for segment in self.segments:
            segment[4].setParent(None)
        self.segments = []
        self.segment_count = 0
        self.segment_count_label.setText(f"已切分的段落: {self.segment_count}")
        if not silent:
            self.flash_message("已清空所有分段")

    # --- 时间戳格式化 ---
    def format_timestamp(self, ms):
        if ms is None:
            return ""
        totalSec = ms // 1000
        ms_remain = ms % 1000
        h = totalSec // 3600
        m = (totalSec % 3600) // 60
        s = totalSec % 60
        return f"{h:02}:{m:02}:{s:02}.{ms_remain:03}"

    def parse_timestamp(self, ts):
        parts = ts.split(':')
        if len(parts) != 3:
            raise ValueError("Invalid timestamp format")
        h, m, s_ms = parts
        s, ms = s_ms.split('.')
        return (int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms)

    # --- 切割视频 ---
    def cut_video(self):
        if not self.video_file:
            QtWidgets.QMessageBox.warning(self, "提示", "请先打开视频")
            return
        if not self.segments:
            QtWidgets.QMessageBox.warning(self, "提示", "没有分段可切割")
            return

        valid_segments = [s for s in self.segments if s[1] is not None and s[2] >= 0]
        if not valid_segments:
            QtWidgets.QMessageBox.warning(self, "提示", "没有有效分段可切割")
            return

        self.flash_message("开始切割视频...")
        ext = os.path.splitext(self.video_file)[1]
        base_name = os.path.splitext(os.path.basename(self.video_file))[0]

        for start_ms, end_ms, duration, tag, widget in valid_segments:  # 修正：正确解包 widget
            start_ts = self.format_timestamp(start_ms).replace(":", "")
            end_ts = self.format_timestamp(end_ms).replace(":", "")
            # 获取 Tag 输入框的当前内容
            tag_widget = widget.layout().itemAt(3).widget()
            tag_input = tag_widget.layout().itemAt(1).widget()
            tag_text = tag_input.text()
            # 使用 Tag 文本框内容，如果为空则使用 "notag"
            tag_clean = tag_text.replace(" ", "_") if tag_text.strip() else "notag"
            output_file = os.path.join(self.video_dir, f"{base_name}-{start_ts}-{end_ts}-{tag_clean}{ext}")
            print(f"[INFO] 切割: {output_file}")
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-i", self.video_file,
                "-ss", self.format_timestamp(start_ms),
                "-to", self.format_timestamp(end_ms),
                "-c", "copy",  # 无损切割
                output_file
            ])
            self.flash_message(f"已生成: {os.path.basename(output_file)}")

        self.flash_message("视频切割完成")

    # --- 键盘操作 ---
    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Left:
            self.player.seek(-5)
        elif event.key() == QtCore.Qt.Key_Right:
            self.player.seek(5)
        elif event.key() == QtCore.Qt.Key_Up:
            self.player.seek(-60)
        elif event.key() == QtCore.Qt.Key_Down:
            self.player.seek(60)
        elif event.key() == QtCore.Qt.Key_Space:
            self.toggle_play_pause()
        elif event.key() == QtCore.Qt.Key_S:
            self.add_segment()
        elif event.key() == QtCore.Qt.Key_E:
            self.close_segment()
        else:
            super().keyPressEvent(event)

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = VideoCutter()
    win.show()
    sys.exit(app.exec_())