import sys
import os
import datetime
import subprocess
from PyQt5 import QtWidgets, QtCore, QtGui
import glob
import shutil

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

class VideoStoryboard(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Storyboard")
        self.resize(1400, 800)

        # --- 主布局，左右可拉伸 ---
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)

        h_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        # --- 左侧控制区 ---
        self.control_widget = QtWidgets.QWidget()
        self.control_widget.setMinimumWidth(300)  # 防止拖得太小
        control_layout = QtWidgets.QVBoxLayout(self.control_widget)
        h_splitter.addWidget(self.control_widget)

        # 状态信息
        self.status_label = QtWidgets.QLabel()
        control_layout.insertWidget(0, self.status_label)

        # 模式选择
        self.mode_group = QtWidgets.QGroupBox("抽帧模式")
        mode_layout = QtWidgets.QHBoxLayout(self.mode_group)
        self.manual_radio = QtWidgets.QRadioButton("手动")
        self.auto_radio = QtWidgets.QRadioButton("自动")
        self.auto_radio.setChecked(True)
        mode_layout.addWidget(self.manual_radio)
        mode_layout.addWidget(self.auto_radio)
        self.mode_group.setLayout(mode_layout)
        control_layout.addWidget(self.mode_group)

        # 自动抽帧设置
        self.auto_group = QtWidgets.QGroupBox("自动抽帧设置")
        auto_layout = QtWidgets.QFormLayout(self.auto_group)
        self.steps_input = QtWidgets.QLineEdit("30")
        self.pattern_combo = QtWidgets.QComboBox()
        self.load_patterns()
        self.browse_pattern_btn = QtWidgets.QPushButton("浏览新Pattern")
        auto_layout.addRow("抽帧数:", self.steps_input)
        auto_layout.addRow("Pattern选择:", self.pattern_combo)
        auto_layout.addRow("", self.browse_pattern_btn)
        self.auto_group.setLayout(auto_layout)
        control_layout.addWidget(self.auto_group)

        # 手动缩略图区域
        self.thumb_scroll = QtWidgets.QScrollArea()
        self.thumb_scroll.setWidgetResizable(True)
        self.thumb_container = QtWidgets.QWidget()
        self.thumb_layout = QtWidgets.QVBoxLayout(self.thumb_container)
        self.thumb_layout.setAlignment(QtCore.Qt.AlignTop)
        self.thumb_container.setLayout(self.thumb_layout)
        self.thumb_scroll.setWidget(self.thumb_container)
        control_layout.addWidget(self.thumb_scroll, 1)

        # 操作按钮
        self.open_btn = QtWidgets.QPushButton("📂 Open")
        self.play_pause_btn = QtWidgets.QPushButton("▶/⏸ Play/Pause")
        self.screenshot_btn = QtWidgets.QPushButton("📸 Screenshot")
        self.auto_snap_btn = QtWidgets.QPushButton("⚡ 自动抽帧")
        self.generate_btn = QtWidgets.QPushButton("🖼 生成StoryBoard")
        control_layout.addWidget(self.open_btn)
        control_layout.addWidget(self.play_pause_btn)
        control_layout.addWidget(self.screenshot_btn)
        control_layout.addWidget(self.auto_snap_btn)
        control_layout.addWidget(self.generate_btn)

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
        self.progress_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.progress_slider.setRange(0, 1000)
        self.progress_slider.setSingleStep(1)
        video_layout.addWidget(self.progress_slider)

        h_splitter.addWidget(self.video_container)

        # 设置初始比例（左:右 = 1:2）
        h_splitter.setSizes([450, 900])

        # 添加 splitter 到 central_widget
        layout = QtWidgets.QHBoxLayout(central_widget)
        layout.addWidget(h_splitter)

        # --- mpv 播放器 ---
        self.player = mpv.MPV(
            wid=str(int(self.video_widget.winId())),
            ytdl=False,
            osc=False,  # 关闭自带 OSC
            log_handler=print,
            loglevel="info"
        )

        # --- 定时器更新进度条 ---
        self.update_timer = QtCore.QTimer()
        self.update_timer.setInterval(200)
        self.update_timer.timeout.connect(self.update_slider)
        self.update_timer.start()

        # 进度条拖动事件
        self.progress_slider.sliderReleased.connect(self.slider_seek)
        self.progress_slider.sliderPressed.connect(self.slider_press)
        self.slider_is_pressed = False

        # --- 绑定按钮 ---
        self.open_btn.clicked.connect(self.open_file)
        self.play_pause_btn.clicked.connect(self.toggle_play_pause)
        self.screenshot_btn.clicked.connect(self.screenshot_video)
        self.auto_snap_btn.clicked.connect(self.auto_snap)
        self.generate_btn.clicked.connect(self.generate_storyboard)
        self.browse_pattern_btn.clicked.connect(self.browse_pattern)

        self.screenshots = []
        self.video_file = None

        # 键盘事件
        self.video_widget.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.video_widget.keyPressEvent = self.keyPressEvent

    # --- 定时器更新进度条 ---
    def update_slider(self):
        if self.video_file and not self.slider_is_pressed:
            if self.player.time_pos is not None and self.player.duration is not None:
                pos = self.player.time_pos / self.player.duration * 1000
                self.progress_slider.setValue(int(pos))

    # --- 用户拖动进度条 ---
    def slider_seek(self):
        if self.video_file and self.player.duration is not None:
            value = self.progress_slider.value()
            t = value / 1000 * self.player.duration
            self.player.seek(t, reference="absolute")
        self.slider_is_pressed = False

    def slider_press(self):
        self.slider_is_pressed = True

    # 显示消息函数
    def flash_message(self, msg, timeout=3000):
        self.status_label.setText(msg)
        self.status_label.setStyleSheet("color: red; font-weight: bold;")
        QtCore.QTimer.singleShot(timeout, lambda: self.status_label.setText(""))

    # --- Pattern管理 ---
    def load_patterns(self):
        self.pattern_combo.clear()
        pattern_dir = os.path.join(os.getcwd(), "pattern")
        self.pattern_files = []
        if os.path.exists(pattern_dir):
            for f in os.listdir(pattern_dir):
                if f.lower().endswith((".jpg", ".png")):
                    path = os.path.join(pattern_dir, f)
                    self.pattern_combo.addItem(f)
                    self.pattern_files.append(path)
        if self.pattern_files:
            self.pattern_combo.setCurrentIndex(0)

    def browse_pattern(self):
        f, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择Pattern图片", "", "图片 (*.jpg *.png)")
        if f:
            self.pattern_combo.addItem(os.path.basename(f))
            self.pattern_files.append(f)
            self.pattern_combo.setCurrentIndex(len(self.pattern_files) - 1)

    # --- 视频操作 ---
    def open_file(self):
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择视频文件", "", "视频文件 (*.mp4 *.mkv *.avi *.mov *.ts)"
        )
        if filename:
            self.player.play(filename)
            self.video_file = filename
            print(f"[INFO] 打开视频: {filename}")

    def toggle_play_pause(self):
        self.player.pause = not self.player.pause

    # --- 截图并添加时间戳 ---
    def screenshot_video(self):
        if self.player.time_pos is None:
            QtWidgets.QMessageBox.warning(self, "提示", "视频尚未播放")
            return
        t_ms = int(self.player.time_pos * 1000)
        totalSec = t_ms // 1000
        ms = t_ms % 1000
        h = totalSec // 3600
        m = (totalSec % 3600) // 60
        s = totalSec % 60
        timestamp = f"{h:02}.{m:02}.{s:02}.{ms:03}"
        outfile = f"Screenshot={timestamp}=.jpg"
        self.player.command("screenshot-to-file", outfile)
        print(f"[INFO] 截图: {outfile}")

        # 添加时间戳
        self.add_timestamp_to_image(outfile, timestamp)
        self.add_thumbnail(outfile)

    def add_timestamp_to_image(self, image_file, timestamp):
        """为截图添加时间戳"""
        print(f"[INFO] 为截图 {image_file} 添加时间戳 {timestamp}")
        # 创建备份目录
        backup_dir = os.path.join(os.getcwd(), "backup")
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
        # 备份原始截图
        backup_file = os.path.join(backup_dir, os.path.basename(image_file))
        try:
            shutil.copy(image_file, backup_file)
            print(f"[INFO] 已备份截图 -> {backup_file}")
        except Exception as e:
            print(f"[WARN] 备份失败: {e}")
                # subprocess.run(["copy", image_file, backup_file], shell=True)
        
        # 写入 keyframes.txt
        keyframes_file = os.path.join(os.getcwd(), "keyframes.txt")
        with open(keyframes_file, "a", encoding="utf-8") as f:
            f.write(timestamp + "\n")
        
        # 使用 ImageMagick 添加时间戳
        subprocess.run([
            "magick", image_file,
            "-resize", "600x",
            "-gravity", "SouthWest",
            "-font", "Consolas-Italic",
            "-pointsize", "24",
            "-fill", "white",
            "-stroke", "black",
            "-strokewidth", "4",
            "-annotate", "+10+10", timestamp,
            "-fill", "white",
            "-stroke", "none",
            "-annotate", "+10+10", timestamp,
            image_file
        ])

    def add_thumbnail(self, filepath):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)
        pixmap = QtGui.QPixmap(filepath).scaledToWidth(120)
        label = QtWidgets.QLabel()
        label.setPixmap(pixmap)
        layout.addWidget(label)
        del_btn = QtWidgets.QPushButton("❌")
        del_btn.setMaximumWidth(24)
        layout.addWidget(del_btn)
        self.thumb_layout.addWidget(widget)
        self.screenshots.append((filepath, widget))
        del_btn.clicked.connect(lambda: self.remove_thumbnail(filepath, widget))

    def remove_thumbnail(self, filepath, widget):
        widget.setParent(None)
        self.screenshots = [x for x in self.screenshots if x[0] != filepath]
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"[INFO] 删除截图: {filepath}")

    # --- 自动抽帧 ---
    def auto_snap(self):
        if not self.video_file:
            QtWidgets.QMessageBox.warning(self, "提示", "请先打开视频")
            return
        steps = 30
        try:
            steps = int(self.steps_input.text())
        except:
            pass
        print(f"[INFO] 自动抽取 {steps} 帧")
        duration_ms = int(subprocess.check_output(["mediainfo", "--Inform=Video;%Duration%", self.video_file]).decode().strip())
        offset = 1000
        times = [offset + int(i * (duration_ms - 2 * offset) / (steps - 1)) for i in range(steps)]
        for idx, t_ms in enumerate(times):
            totalSec = t_ms // 1000
            ms = t_ms % 1000
            h = totalSec // 3600
            m = (totalSec % 3600) // 60
            s = totalSec % 60
            timestamp = f"{h:02}.{m:02}.{s:02}.{ms:03}"
            outfile = f"Screenshot={timestamp}=.jpg"
            print(f"[INFO] [{idx+1}/{len(times)}] 截图时间 {h:02}:{m:02}:{s:02}.{ms:03} -> {outfile}")
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-ss", f"{h:02}:{m:02}:{s:02}.{ms:03}",
                "-i", self.video_file,
                "-frames:v", "1",
                "-q:v", "2",
                outfile
            ])
            # 添加时间戳
            self.add_timestamp_to_image(outfile, timestamp)
            self.add_thumbnail(outfile)
        self.flash_message("自动抽帧完成")

    # --- 生成视频信息图片 ---
    def generate_video_info_image(self):
        if not self.video_file:
            return None
        print("[INFO] 生成视频信息图片...")
        output_txt = os.path.join(os.getcwd(), "output.txt")
        # 调用 mediainfo 生成文本信息
        subprocess.run(f'mediainfo --Inform="file://template_mediainfo.txt" "{self.video_file}" > "{output_txt}"', shell=True)
        out_img = os.path.join(os.getcwd(), "out.png")
        # 用 ImageMagick 生成透明背景图片
        subprocess.run([
            "magick", "-size", "1920x320", "xc:transparent",
            "-font", "YaHei-Consolas-Hybrid.ttf",
            "-fill", "white", "-pointsize", "24",
            "-stroke", "black", "-strokewidth", "2",
            "-annotate", "+60+60", "@" + output_txt,
            "-fill", "white", "-stroke", "none",
            "-annotate", "+60+60", "@" + output_txt,
            out_img
        ])
        return out_img

    # --- 生成最终Storyboard ---
    def generate_storyboard(self):
        if not self.screenshots:
            QtWidgets.QMessageBox.warning(self, "提示", "没有截图")
            return
        # 生成视频信息图片
        info_img = self.generate_video_info_image()
        files = [x[0] for x in self.screenshots]
        montage_file = "montaged.png"
        print("[INFO] 拼接截图...")
        subprocess.run(["magick", "montage"] + files + ["-background", "none", "-geometry", "600x+5+5", "-tile", "3x", montage_file])
        # 扩展到 1920 宽度，垂直居中
        subprocess.run([
            "magick", montage_file,
            "-background", "none",
            "-gravity", "center",
            "-extent", "1920x",
            montage_file
        ])
        # 合并视频信息图片和 montage
        if info_img and os.path.exists(info_img):
            subprocess.run(["magick", "montage", info_img, montage_file, "-background", "none", "-geometry", "+0+0", "-tile", "1x2", "Snaps.png"])
            final_input = "Snaps.png"
        else:
            final_input = montage_file

        # Pattern处理
        pattern_idx = self.pattern_combo.currentIndex()
        if pattern_idx >= 0 and pattern_idx < len(self.pattern_files):
            pattern_file = self.pattern_files[pattern_idx]
        else:
            pattern_file = None
        width, height = map(int, subprocess.check_output(["magick", "identify", "-format", "%w %h", final_input]).decode().strip().split())
        if pattern_file:
            subprocess.run(["magick", "-size", f"{width}x{height}", "tile:" + pattern_file, "Tiles.jpg"])
        else:
            subprocess.run(["magick", "-size", f"{width}x{height}", "canvas:white", "Tiles.jpg"])
        final_file = f"Storyboard-{os.path.basename(self.video_file)}.jpg"
        subprocess.run(["magick", "composite", "-type", "truecolor", final_input, "Tiles.jpg", final_file])
        self.flash_message(f"生成 Storyboard: {final_file}")
        print(f"[INFO] 完成！输出文件: {final_file}")

        # --- 清理临时文件 ---
        temp_files = ["out.png", "output.txt", "montaged.png", "Snaps.png", "Tiles.jpg"]
        temp_files += glob.glob("Screenshot=*.jpg")
        for f in temp_files:
            try:
                os.remove(f)
                print(f"[INFO] 删除临时文件: {f}")
            except FileNotFoundError:
                pass

    # --- 键盘操作 ---
    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Left:
            self.player.seek(-5)
        elif event.key() == QtCore.Qt.Key_Right:
            self.player.seek(5)
        elif event.key() == QtCore.Qt.Key_Space:
            self.toggle_play_pause()
        elif event.key() == QtCore.Qt.Key_S:
            self.screenshot_video()
        else:
            super().keyPressEvent(event)

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = VideoStoryboard()
    win.show()
    sys.exit(app.exec_())