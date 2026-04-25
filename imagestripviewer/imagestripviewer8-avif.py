import sys
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTreeView, QListWidget, QListWidgetItem, 
                             QSplitter, QGraphicsView, QGraphicsScene, 
                             QGraphicsItem, QPushButton, QFrame, QStackedWidget)
from PyQt6.QtGui import (QPixmap, QIcon, QAction, QWheelEvent, QColor, QBrush, 
                         QFileSystemModel, QImageReader, QPainter, QImage)
from PyQt6.QtCore import Qt, QDir, QSize, QTimer, QPoint, QRectF, QSettings
from PIL import Image
import pillow_avif # pip install Pillow pillow-avif-plugin
import io

# 支持的图片格式
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.svg', '.avif'}
def load_pixmap_universal(path):
    """
    通用加载函数：先尝试 Qt 原生加载，失败则尝试 Pillow 加载
    """
    pixmap = QPixmap(path)
    if not pixmap.isNull():
        return pixmap
    
    # 如果 Qt 加载失败（比如 AVIF），使用 Pillow
    try:
        pil_img = Image.open(path).convert("RGBA")
        data = pil_img.tobytes("raw", "RGBA")
        qimg = QImage(data, pil_img.size[0], pil_img.size[1], QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(qimg)
    except Exception as e:
        print(f"无法加载图片 {path}: {e}")
        return None
    
class LazyGraphicsItem(QGraphicsItem):
    """
    懒加载图元：
    初始化时只读取图片尺寸（极快），只有在 paint 被调用时才真正加载图片数据。
    """
    def __init__(self, path, target_size, is_horizontal):
        super().__init__()
        self.path = path
        self.pixmap = None
        self.loaded = False
        
        # 1. 快速获取图片尺寸，不解码像素
        reader = QImageReader(path)
        size = reader.size()
        # 尝试获取尺寸
        try:
            with Image.open(path) as img:
                orig_w, orig_h = img.size
        except:
            orig_w, orig_h = 100, 100
        # if size.isValid():
        #     orig_w, orig_h = size.width(), size.height()
        # else:
        #     orig_w, orig_h = 100, 100 # 错误兜底
            
        # 2. 预计算缩放后的尺寸和位置
        self.is_horizontal = is_horizontal
        if is_horizontal:
            # 高度撑满，宽度等比
            if orig_h > 0:
                self.scale_factor = target_size / orig_h
            else:
                self.scale_factor = 1
            self.display_w = orig_w * self.scale_factor
            self.display_h = target_size
        else:
            # 宽度撑满，高度等比
            if orig_w > 0:
                self.scale_factor = target_size / orig_w
            else:
                self.scale_factor = 1
            self.display_w = target_size
            self.display_h = orig_h * self.scale_factor
            
        # 启用缓存，优化重绘性能
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)

    def boundingRect(self):
        # 告诉视图这个东西有多大（即使还没加载图片）
        return QRectF(0, 0, self.display_w, self.display_h)

    def paint(self, painter, option, widget):
        # 3. 只有当真正要显示时，才去加载 QPixmap
        if not self.loaded:
            self.pixmap = load_pixmap_universal(self.path)
            self.loaded = True
            
        if self.pixmap and not self.pixmap.isNull():
            # 启用高质量缩放
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            # 绘制图片到指定区域
            target_rect = QRectF(0, 0, self.display_w, self.display_h)
            painter.drawPixmap(target_rect, self.pixmap, QRectF(self.pixmap.rect()))
        else:
            # 加载失败显示占位
            painter.fillRect(self.boundingRect(), QColor("#333"))
            painter.setPen(QColor("#666"))
            painter.drawText(self.boundingRect(), Qt.AlignmentFlag.AlignCenter, "无法加载")

class SmoothScrollView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene()
        self.setScene(self.scene)
        
        # 视觉设置
        self.setBackgroundBrush(QBrush(QColor("#000000")))
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        
        # --- 关键修正1：开启鼠标追踪，解决工具栏不弹出问题 ---
        self.setMouseTracking(True) 

        # 优化渲染参数
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        
        # 数据状态
        self.image_paths = []
        self.current_index = 0
        self.mode = 0
        self.current_item = None
        
        # 物理滚动参数
        self.scroll_velocity = 0
        self.friction = 0.85      # 摩擦力 (0.9 -> 0.88 刹车更快一点)
        self.sensitivity = 0.25   # 灵敏度 (1.5 -> 0.15 降低10倍，解决速度过快)
        self.max_speed = 80       # 最大速度限制 (防止疯转)
        
        self.physics_timer = QTimer(self)
        self.physics_timer.setInterval(7)
        self.physics_timer.timeout.connect(self.update_physics)
        
        self.mouse_moved_callback = None
        self.item_positions = []

        # --- 关键修正2：沉浸式光标定时器 ---
        self.cursor_hide_timer = QTimer(self)
        self.cursor_hide_timer.setInterval(1000) # 1秒后隐藏
        self.cursor_hide_timer.timeout.connect(self.hide_cursor)

    def load_images(self, paths, index, mode):
        self.image_paths = paths
        self.current_index = index
        self.change_mode(mode)
        # 加载新图片时重置光标
        self.show_cursor()

    def change_mode(self, mode):
        self.mode = mode
        self.scene.clear()
        self.scroll_velocity = 0
        self.physics_timer.stop()
        self.resetTransform()
        self.item_positions = []
        if not self.image_paths: return
        if mode == 0: self.show_single_image(self.current_index)
        elif mode == 1: self.setup_continuous_mode(horizontal=True)
        elif mode == 2: self.setup_continuous_mode(horizontal=False)

    def show_single_image(self, index):
        if 0 <= index < len(self.image_paths):
            self.scene.clear()
            self.current_index = index
            pixmap = load_pixmap_universal(self.image_paths[index])
            if not pixmap.isNull():
                self.current_item = self.scene.addPixmap(pixmap)
                self.setSceneRect(self.current_item.boundingRect())
                QTimer.singleShot(0, self._fit_current_item)

    def _fit_current_item(self):
        if self.current_item:
            self.fitInView(self.current_item, Qt.AspectRatioMode.KeepAspectRatio)

    def setup_continuous_mode(self, horizontal=True):
        current_pos = 0
        viewport_rect = self.viewport().rect()
        target_size = viewport_rect.height() if horizontal else viewport_rect.width()
        for i, path in enumerate(self.image_paths):
            item = LazyGraphicsItem(path, target_size, horizontal)
            self.scene.addItem(item)
            if horizontal:
                item.setPos(current_pos, 0)
                center_coord = current_pos + item.display_w / 2
                self.item_positions.append((i, center_coord))
                current_pos += item.display_w
            else:
                item.setPos(0, current_pos)
                center_coord = current_pos + item.display_h / 2
                self.item_positions.append((i, center_coord))
                current_pos += item.display_h

        if horizontal:
            self.setSceneRect(0, 0, current_pos, viewport_rect.height())
            if 0 <= self.current_index < len(self.item_positions):
                self.centerOn(self.item_positions[self.current_index][1], 0)
        else:
            self.setSceneRect(0, 0, viewport_rect.width(), current_pos)
            if 0 <= self.current_index < len(self.item_positions):
                self.centerOn(0, self.item_positions[self.current_index][1])

    def resizeEvent(self, event):
        if self.mode != 0: self.change_mode(self.mode) 
        elif self.mode == 0 and self.current_item: QTimer.singleShot(0, self._fit_current_item)
        super().resizeEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        # 注意：滚轮事件中不调用 show_cursor()，实现滚动时不显示光标
        if self.mode == 0:
            delta = event.angleDelta().y()
            if delta > 0: self.change_image(-1)
            else: self.change_image(1)
        else:
            delta = event.angleDelta().y()
            self.scroll_velocity += delta * self.sensitivity
            self.scroll_velocity = max(min(self.scroll_velocity, self.max_speed), -self.max_speed)
            if not self.physics_timer.isActive():
                self.physics_timer.start()

    def update_physics(self):
        if abs(self.scroll_velocity) < 0.5:
            self.scroll_velocity = 0
            self.physics_timer.stop()
            return
        
        if self.mode == 1:
            current_center = self.mapToScene(self.viewport().rect().center())
            new_x = current_center.x() - self.scroll_velocity
            new_x = max(0, min(new_x, self.sceneRect().width()))
            self.centerOn(new_x, 0)
        elif self.mode == 2:
            current_center = self.mapToScene(self.viewport().rect().center())
            new_y = current_center.y() - self.scroll_velocity
            new_y = max(0, min(new_y, self.sceneRect().height()))
            self.centerOn(0, new_y)
        self.scroll_velocity *= self.friction

    def change_image(self, offset):
        new_index = self.current_index + offset
        if 0 <= new_index < len(self.image_paths):
            self.show_single_image(new_index)

    def mouseDoubleClickEvent(self, event):
        self.parent().toggle_fullscreen()
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        # 任何按键都唤醒光标
        self.show_cursor()
        
        key = event.key()

        # --- 新增快捷键绑定 ---
        if key == Qt.Key.Key_S:
            self.parent().switch_mode(0)  # S -> 单图模式
            event.accept()
            return
        elif key == Qt.Key.Key_H:
            self.parent().switch_mode(1)  # H -> 横向卷轴
            event.accept()
            return
        elif key == Qt.Key.Key_V:
            self.parent().switch_mode(2)  # V -> 纵向卷轴
            event.accept()
            return
        # ---------------------

        # 原有逻辑：退出/全屏
        if key == Qt.Key.Key_Escape:
            if self.window().isFullScreen():
                self.parent().toggle_fullscreen()
            else:
                self.parent().exit_viewer()
            event.accept()
            return
        elif key == Qt.Key.Key_F11:
            self.parent().toggle_fullscreen()
            event.accept()
            return
        
        # 原有逻辑：单图模式下的方向键翻页
        elif self.mode == 0:
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
                self.change_image(-1)
                event.accept()
                return
            elif key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
                self.change_image(1)
                event.accept()
                return
        
        super().keyPressEvent(event)

    # --- 光标与移动逻辑 ---
    def mouseMoveEvent(self, event):
        # 只要移动鼠标，就显示光标并重置计时
        self.show_cursor()
        
        # 触发工具栏检测
        if self.mouse_moved_callback:
            self.mouse_moved_callback(event.pos())
        super().mouseMoveEvent(event)

    def show_cursor(self):
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.cursor_hide_timer.start() # 重新开始1秒倒计时

    def hide_cursor(self):
        # 只有在全屏模式下才隐藏光标
        if self.window().isFullScreen():
            # 检查鼠标是否在工具栏区域，如果在上面则不隐藏
            # 获取局部坐标
            local_pos = self.mapFromGlobal(self.cursor().pos())
            if local_pos.y() > 80: # 避开顶部工具栏区域
                self.viewport().setCursor(Qt.CursorShape.BlankCursor)


class ViewerOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        self.view = SmoothScrollView(self)
        self.view.mouse_moved_callback = self.check_toolbar_trigger
        self.layout.addWidget(self.view)
        
        self.toolbar = QFrame(self)
        self.toolbar.setStyleSheet("""
            QFrame {
                background-color: rgba(30, 30, 30, 200);
                border-bottom-left-radius: 10px;
                border-bottom-right-radius: 10px;
            }
            QPushButton {
                background: transparent;
                border: 1px solid #666;
                color: #ddd;
                border-radius: 15px;
                padding: 5px 15px;
                margin: 0 5px;
            }
            QPushButton:hover { background-color: #444; }
            QPushButton:checked { background-color: #007acc; border-color: #007acc; color: white; }
        """)
        self.toolbar_layout = QHBoxLayout(self.toolbar)
        
        self.btn_single = QPushButton("单图模式")
        self.btn_single.setCheckable(True)
        self.btn_h = QPushButton("横向卷轴")
        self.btn_h.setCheckable(True)
        self.btn_v = QPushButton("纵向卷轴")
        self.btn_v.setCheckable(True)
        self.btn_close = QPushButton("关闭 (Esc)")
        self.btn_close.setStyleSheet("color: #ff5555; border-color: #ff5555;")
        
        self.toolbar_layout.addWidget(self.btn_single)
        self.toolbar_layout.addWidget(self.btn_h)
        self.toolbar_layout.addWidget(self.btn_v)
        self.toolbar_layout.addWidget(self.btn_close)
        
        self.btn_single.clicked.connect(lambda: self.switch_mode(0))
        self.btn_h.clicked.connect(lambda: self.switch_mode(1))
        self.btn_v.clicked.connect(lambda: self.switch_mode(2))
        self.btn_close.clicked.connect(self.exit_viewer)
        
        self.hide_timer = QTimer(self)
        self.hide_timer.setInterval(3000)
        self.hide_timer.timeout.connect(self.hide_toolbar)
        
        self.toolbar_visible = False
        self.toolbar.hide()  
        
    def resizeEvent(self, event):
        w = 500
        h = 60
        x = (self.width() - w) // 2
        self.toolbar.setGeometry(x, 0, w, h)
        super().resizeEvent(event)

    def load_data(self, paths, index):
        self.view.load_images(paths, index, 1)
        self.switch_mode(1)
        self.show_toolbar()

    def switch_mode(self, mode):
        self.btn_single.setChecked(mode == 0)
        self.btn_h.setChecked(mode == 1)
        self.btn_v.setChecked(mode == 2)
        self.view.change_mode(mode)
        # 通知 MainWindow 保存当前模式
        self.parent().parent().save_mode_setting(mode)

    def exit_viewer(self):
        self.hide_timer.stop()
        self.view.show_cursor() # 退出前恢复光标
        self.parent().parent().switch_to_gallery()

    def toggle_fullscreen(self):
        self.parent().parent().toggle_fullscreen()
        # 切换全屏状态时，如果退出了全屏，确保光标显示
        if not self.window().isFullScreen():
            self.view.show_cursor()

    def check_toolbar_trigger(self, pos: QPoint):
        # 简化逻辑：只要在顶部区域，就强制显示，不管之前状态如何
        if pos.y() < 80:
            self.show_toolbar()
        # 如果离开了顶部区域，且工具栏开着，且没有在倒计时，就开始倒计时
        elif self.toolbar_visible and not self.hide_timer.isActive():
            self.hide_timer.start()

    def show_toolbar(self):
        if not self.toolbar_visible:
            self.toolbar.show()
            self.toolbar_visible = True
        # 只要触发显示，就重置/保持倒计时（这样鼠标一直在顶部动时，工具栏常驻）
        self.hide_timer.start()

    def hide_toolbar(self):
        # 如果鼠标在工具栏上，不隐藏
        if self.toolbar.underMouse():
            self.hide_timer.start()
            return
        # 如果鼠标还在顶部感应区（例如从按钮移开但还在顶部80px内），也不隐藏
        global_mouse = self.cursor().pos()
        local_mouse = self.view.mapFromGlobal(global_mouse)
        if local_mouse.y() < 80:
            self.hide_timer.start()
            return

        self.toolbar.hide()
        self.toolbar_visible = False
        self.hide_timer.stop()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("卷舒看图")
        self.resize(1200, 800)
        self.setStyleSheet("background-color: #ffffff; color: #333;")

        # 设置配置
        self.settings = QSettings("ImageViewer", "LastPath")

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        # --- 浏览器界面布局 ---
        self.browser_widget = QWidget()
        self.browser_layout = QVBoxLayout(self.browser_widget)
        self.browser_layout.setContentsMargins(0, 0, 0, 0)
        self.browser_layout.setSpacing(0)

        # 地址栏
        from PyQt6.QtWidgets import QLineEdit
        self.address_bar = QLineEdit()
        self.address_bar.setPlaceholderText("在此粘贴路径并回车...")
        self.address_bar.setStyleSheet("""
            QLineEdit { padding: 10px; border: none; border-bottom: 1px solid #ddd; font-size: 13px; background: #fff; }
            QLineEdit:focus { background: #fdfdfd; border-bottom: 1px solid #007acc; }
        """)
        self.address_bar.returnPressed.connect(self.on_address_entered)
        self.browser_layout.addWidget(self.address_bar)
        
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # 左侧文件树
        self.dir_model = QFileSystemModel()
        self.dir_model.setRootPath('') 
        self.dir_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives)
        
        self.tree = QTreeView()
        self.tree.setModel(self.dir_model)
        for i in range(1, 4):
            self.tree.setColumnHidden(i, True)
        self.tree.setHeaderHidden(True)
        self.tree.clicked.connect(self.on_tree_click)
        self.tree.setStyleSheet("background-color: #f5f5f5; border: none; font-size: 13px; color: #333; selection-background-color: #007acc;")
        
        # 右侧列表
        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setIconSize(QSize(140, 140))
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setSpacing(10)
        self.list_widget.setStyleSheet("""
            QListWidget { background-color: #ffffff; border: none; }
            QListWidget::item { background: #f5f5f5; border-radius: 5px; }
            QListWidget::item:hover { background: #e5e5e5; }
            QListWidget::item:selected { background: #007acc; }
        """)
        self.list_widget.itemDoubleClicked.connect(self.on_thumb_double_click)

        self.splitter.addWidget(self.tree)
        self.splitter.addWidget(self.list_widget)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 4)
        self.browser_layout.addWidget(self.splitter)
        
        self.viewer_overlay = ViewerOverlay(self)
        self.stack.addWidget(self.browser_widget)
        self.stack.addWidget(self.viewer_overlay)
        
        self.current_folder_files = []
        self.is_fullscreen = False
        
        # 恢复上次打开的目录和启动参数
        self.restore_last_path()

    def restore_last_path(self):
        args = sys.argv[1:]
        startup_path = ""
        target_file = ""

        if args:
            input_path = os.path.abspath(args[0].strip('"'))
            if os.path.isfile(input_path):
                startup_path = os.path.dirname(input_path)
                target_file = input_path
            elif os.path.isdir(input_path):
                startup_path = input_path

        if not startup_path:
            startup_path = self.settings.value("last_directory", "")

        if startup_path and os.path.isdir(startup_path):
            self.jump_to_path(startup_path)
            if target_file:
                items = self.list_widget.findItems(os.path.basename(target_file), Qt.MatchFlag.MatchExactly)
                if items:
                    self.list_widget.setCurrentItem(items[0])
                    self.list_widget.scrollToItem(items[0])

    def jump_to_path(self, path):
        index = self.dir_model.index(path)
        if index.isValid():
            self.tree.setCurrentIndex(index)
            self.tree.scrollTo(index)
            self.load_thumbnails(path)
            self.address_bar.setText(path)
            self.settings.setValue("last_directory", path)

    def on_address_entered(self):
        path = self.address_bar.text().strip().replace('"', '')
        if os.path.isdir(path):
            self.jump_to_path(path)

    def on_tree_click(self, index):
        path = self.dir_model.fileInfo(index).absoluteFilePath()
        self.address_bar.setText(path)
        self.load_thumbnails(path)
        self.settings.setValue("last_directory", path)

    def load_thumbnails(self, folder_path):
        self.list_widget.clear()
        self.current_folder_files = []
        if not os.path.isdir(folder_path): return

        # 自然排序
        import re
        def natural_sort_key(s):
            return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]

        try:
            files = os.listdir(folder_path)
            images = [f for f in files if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS]
            images.sort(key=natural_sort_key)
            
            thumb_size = self.list_widget.iconSize()
            for img_name in images:
                full_path = os.path.join(folder_path, img_name)
                self.current_folder_files.append(full_path)
                pixmap = load_pixmap_universal(full_path)
                if not pixmap.isNull():
                    icon = QIcon(pixmap.scaled(thumb_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                    item = QListWidgetItem(icon, img_name)
                else:
                    item = QListWidgetItem(img_name)
                self.list_widget.addItem(item)
        except Exception as e:
            print(f"Error: {e}")

    def on_thumb_double_click(self, item):
        index = self.list_widget.row(item)
        # --- 功能：读取上次记住的模式 ---
        last_mode = int(self.settings.value("viewer_mode", 1)) # 默认横向卷轴
        self.viewer_overlay.load_data(self.current_folder_files, index)
        self.viewer_overlay.switch_mode(last_mode)
        self.stack.setCurrentIndex(1)

    def save_mode_setting(self, mode):
        """供 ViewerOverlay 调用以保存模式"""
        self.settings.setValue("viewer_mode", mode)

    def switch_to_gallery(self):
        if self.is_fullscreen: self.toggle_fullscreen()
        self.stack.setCurrentIndex(0)

    def toggle_fullscreen(self):
        if self.is_fullscreen:
            if getattr(self, '_was_maximized_before_fs', False): self.showMaximized()
            else: self.showNormal()
            self.is_fullscreen = False
        else:
            self._was_maximized_before_fs = self.isMaximized()
            self.showFullScreen()
            self.is_fullscreen = True

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F11: self.toggle_fullscreen()
        super().keyPressEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())