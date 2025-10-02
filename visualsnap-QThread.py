import sys
import os
import datetime
import subprocess
import shutil
import glob
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

class StoryboardWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main = parent  # 传入主窗口引用

    def run(self):
        main = self.main
        if not main.screenshots:
            self.finished.emit("没有截图，无法生成 Storyboard")
            return

        # 生成视频信息图片
        self.progress.emit("[INFO] 生成视频信息图片...")
        info_img = main.generate_video_info_image()

        # 拼接截图
        files = [x[0] for x in main.screenshots]
        montage_file = os.path.join(main.video_dir, "montaged.png")
        self.progress.emit("[INFO] 拼接截图...")
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
            snaps_file = os.path.join(main.video_dir, "Snaps.png")
            subprocess.run(["magick", "montage", info_img, montage_file, "-background", "none", "-geometry", "+0+0", "-tile", "1x2", snaps_file])
            final_input = snaps_file
        else:
            final_input = montage_file

        # Pattern处理
        pattern_idx = main.pattern_combo.currentIndex()
        if pattern_idx >= 0 and pattern_idx < len(main.pattern_files):
            pattern_file = main.pattern_files[pattern_idx]
        else:
            pattern_file = None
        width, height = map(int, subprocess.check_output(["magick", "identify", "-format", "%w %h", final_input]).decode().strip().split())
        tiles_file = os.path.join(main.video_dir, "Tiles.jpg")
        if pattern_file:
            subprocess.run(["magick", "-size", f"{width}x{height}", "tile:" + pattern_file, tiles_file])
        else:
            subprocess.run(["magick", "-size", f"{width}x{height}", "canvas:white", tiles_file])

        final_file = os.path.join(main.video_dir, f"Storyboard-{os.path.basename(main.video_file)}.jpg")
        subprocess.run(["magick", "composite", "-type", "truecolor", final_input, tiles_file, final_file])

        self.progress.emit(f"生成 Storyboard: {final_file}")
        print(f"[INFO] 完成！输出文件: {final_file}")

        # --- 清理临时文件 ---
        temp_files = [
            os.path.join(main.video_dir, f) for f in ["out.png", "output.txt", "montaged.png", "Snaps.png", "Tiles.jpg"]
        ]
        temp_files += glob.glob(os.path.join(main.video_dir, "Screenshot=*.jpg"))
        for f in temp_files:
            try:
                os.remove(f)
            except FileNotFoundError:
                pass

        self.finished.emit(final_file)
        
class VideoStoryboard(QtWidgets.QMainWindow):
    flash_signal = QtCore.pyqtSignal(str)  # ✅ 定义信号，放在类体里
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Storyboard")
        self.setStatusBar(QtWidgets.QStatusBar())
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

        # 自动抽帧设置
        self.auto_group = QtWidgets.QGroupBox("自动抽帧设置")
        auto_layout = QtWidgets.QFormLayout(self.auto_group)
        self.steps_input = QtWidgets.QLineEdit("30")

        # --- Pattern 预览 + 下拉框 ---
        pattern_row_widget = QtWidgets.QWidget()
        pattern_row_layout = QtWidgets.QHBoxLayout(pattern_row_widget)
        pattern_row_layout.setContentsMargins(0, 0, 0, 0)

        # 预览窗口 (64x64)
        self.pattern_preview = QtWidgets.QLabel()
        self.pattern_preview.setFixedSize(64, 64)
        self.pattern_preview.setStyleSheet("border: 1px solid gray; background: #202020;")
        self.pattern_preview.setAlignment(QtCore.Qt.AlignCenter)
        pattern_row_layout.addWidget(self.pattern_preview)

        # 下拉菜单
        self.pattern_combo = QtWidgets.QComboBox()
        pattern_row_layout.addWidget(self.pattern_combo, 1)

        auto_layout.addRow("抽帧数:", self.steps_input)
        auto_layout.addRow("Pattern选择:", pattern_row_widget)

        # 浏览按钮
        self.browse_pattern_btn = QtWidgets.QPushButton("浏览新Pattern")
        auto_layout.addRow("", self.browse_pattern_btn)

        self.auto_group.setLayout(auto_layout)
        control_layout.addWidget(self.auto_group)

        # 加载 Pattern
        self.load_patterns()

        # --- 添加帧数计数器 ---
        self.frame_count = 0  # 初始化帧数
        self.frame_count_label = QtWidgets.QLabel(f"当前帧数: {self.frame_count}")
        self.frame_count_label.setStyleSheet("font-weight: bold;")  # 可选：加粗显示
        control_layout.addWidget(self.frame_count_label)

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
        self.open_btn = QtWidgets.QPushButton("📂 打开文件")
        self.play_pause_btn = QtWidgets.QPushButton("⏯️ 播放/暂停")
        self.screenshot_btn = QtWidgets.QPushButton("📸 手动抽帧")
        self.auto_snap_btn = QtWidgets.QPushButton("⚡ 自动抽帧")
        self.generate_btn = QtWidgets.QPushButton("🖼 生成故事板")
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
        # self.progress_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.progress_slider = CustomSlider(QtCore.Qt.Horizontal)
        self.progress_slider.setRange(0, 1000)
        self.progress_slider.setSingleStep(1)
        self.progress_slider.setTracking(True)  # 确保点击立即更新值
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

        # 进度条事件
        self.progress_slider.valueChanged.connect(self.slider_seek)  # 监听 valueChanged
        self.progress_slider.sliderPressed.connect(self.slider_press)
        self.progress_slider.sliderReleased.connect(self.slider_released)  # 添加释放信号
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
        self.video_dir = None  # 新增：存储视频文件所在目录

        # 键盘事件
        self.video_widget.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.video_widget.keyPressEvent = self.keyPressEvent
        

    # --- 定时器更新进度条 ---
    def update_slider(self):
        if self.video_file and not self.slider_is_pressed:
            if self.player.time_pos is not None and self.player.duration is not None:
                pos = self.player.time_pos / self.player.duration * 1000
                self.progress_slider.blockSignals(True)  # 阻止 valueChanged 触发
                self.progress_slider.setValue(int(pos))
                self.progress_slider.blockSignals(False)  # 恢复信号

    # --- 用户拖动或点击进度条 ---
    # def slider_seek(self, value):
    #     if self.video_file and self.player.duration is not None:
    #         t = value / 1000 * self.player.duration
    #         self.player.seek(t, reference="absolute")
    def slider_seek(self, value):
        if self.video_file and self.player.duration is not None:
            self.update_timer.stop()  # 暂停定时器
            t = value / 1000.0 * self.player.duration
            print(f"[DEBUG] Seek to {t:.2f}s (slider value: {value}, duration: {self.player.duration})")
            self.player.seek(t, reference="absolute")  # 移除 precise=True
            QtCore.QTimer.singleShot(500, self.update_timer.start)  # 500ms 后恢复定时器
    def slider_press(self):
        self.slider_is_pressed = True
        
    def slider_released(self):
        self.slider_is_pressed = False
    # 显示消息函数
    def flash_message(self, msg, timeout=3000):
        # self.status_label.setText(msg)
        # self.status_label.setStyleSheet("color: red; font-weight: bold;")
        # QtCore.QTimer.singleShot(timeout, lambda: self.status_label.setText(""))
        self.statusBar().showMessage(msg, timeout)

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
            self.update_pattern_preview()

        # 绑定切换事件
        self.pattern_combo.currentIndexChanged.connect(self.update_pattern_preview)

    def browse_pattern(self):
        f, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择Pattern图片", "", "图片 (*.jpg *.png)")
        if f:
            self.pattern_combo.addItem(os.path.basename(f))
            self.pattern_files.append(f)
            self.pattern_combo.setCurrentIndex(len(self.pattern_files) - 1)
            self.update_pattern_preview()

    def update_pattern_preview(self):
        idx = self.pattern_combo.currentIndex()
        if idx >= 0 and idx < len(self.pattern_files):
            pix = QtGui.QPixmap(self.pattern_files[idx]).scaled(
                64, 64, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
            )
            self.pattern_preview.setPixmap(pix)
        else:
            self.pattern_preview.clear()


    # --- 视频操作 ---
    def open_file(self):
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择视频文件", "", "视频文件 (*.mp4 *.mkv *.avi *.mov *.ts)"
        )
        if filename:
            self.player.play(filename)
            self.video_file = filename
            self.video_dir = os.path.dirname(filename)  # 存储视频文件目录
            print(f"[INFO] 打开视频: {filename}, 目录: {self.video_dir}")

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
        outfile = os.path.join(self.video_dir, f"Screenshot={timestamp}=.jpg")
        self.player.command("screenshot-to-file", outfile)
        print(f"[INFO] 截图: {outfile}")

        # 添加时间戳
        self.add_timestamp_to_image(outfile, timestamp)
        self.add_thumbnail(outfile)

    def add_timestamp_to_image(self, image_file, timestamp):
        """为截图添加时间戳"""
        print(f"[INFO] 为截图 {image_file} 添加时间戳 {timestamp}")
        # 创建备份目录
        backup_dir = os.path.join(self.video_dir, "backup")
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
        # 备份原始截图
        backup_file = os.path.join(backup_dir, os.path.basename(image_file))
        try:
            shutil.copy(image_file, backup_file)
            print(f"[INFO] 已备份截图 -> {backup_file}")
        except Exception as e:
            print(f"[WARN] 备份失败: {e}")

        # 写入 keyframes.txt
        keyframes_file = os.path.join(self.video_dir, "keyframes.txt")
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
        self.frame_count += 1  # 增加帧数计数
        self.frame_count_label.setText(f"当前帧数: {self.frame_count}")  # 更新显示
        del_btn.clicked.connect(lambda: self.remove_thumbnail(filepath, widget))

    def remove_thumbnail(self, filepath, widget):
        widget.setParent(None)
        self.screenshots = [x for x in self.screenshots if x[0] != filepath]
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"[INFO] 删除截图: {filepath}")
        self.frame_count -= 1  # 减少帧数计数
        self.frame_count_label.setText(f"当前帧数: {self.frame_count}")  # 更新显示

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
        self.flash_message(f"[INFO] 自动抽取 {steps} 帧")
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
            outfile = os.path.join(self.video_dir, f"Screenshot={timestamp}=.jpg")
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
        # print(os.path.basename(self.video_file))
        self.flash_signal.emit("[INFO] 生成视频信息图片...")
        output_txt = os.path.join(self.video_dir, "output.txt")
        # 调用 mediainfo 生成文本信息
        subprocess.run(f'mediainfo --Inform="file://template_mediainfo.txt" "{self.video_file}" > "{output_txt}"', shell=True)
        # 读取文件内容，替换路径为纯文件名
        with open(output_txt, "r", encoding="utf-8") as f:
            content = f.read()
        basename = os.path.basename(self.video_file)
        content = content.replace(self.video_file, basename)
        with open(output_txt, "w", encoding="utf-8") as f:
            f.write(content)
        
        out_img = os.path.join(self.video_dir, "out.png")
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
        self.worker = StoryboardWorker(self)
        self.worker.progress.connect(self.flash_message)
        self.worker.finished.connect(self.on_storyboard_finished)
        self.worker.start()
    # --- 完成信号处理函数 ---
    def on_storyboard_finished(self, final_file):
        self.flash_message(f"完成生成: {final_file}")
 
 
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
            self.screenshot_video()
        else:
            super().keyPressEvent(event)

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = VideoStoryboard()
    win.show()
    sys.exit(app.exec_())