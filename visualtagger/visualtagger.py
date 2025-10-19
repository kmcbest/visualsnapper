# image_tag_editor.py
# 依赖: PyQt5, Pillow
# pip install PyQt5 Pillow

import sys
import os
import io
from pathlib import Path
from PIL import Image
from PyQt5 import QtWidgets, QtGui, QtCore
from PyQt5.QtGui import QKeyEvent
import xml.etree.ElementTree as ET
import configparser

# Optional IPTC (读取) — 如果安装了 iptcinfo3 会尝试读取 IPTC 关键字
try:
    from iptcinfo3 import IPTCInfo
    HAS_IPTC = True
except Exception:
    HAS_IPTC = False

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.gif'}

class MasonryView(QtWidgets.QGraphicsView):
    itemClicked = QtCore.pyqtSignal(str)  # 图片选择信号，传递图片路径
    itemDoubleClicked = QtCore.pyqtSignal(str)  # 图片双击信号，传递图片路径
    selectionChanged = QtCore.pyqtSignal(list)  # 多选改变信号，传递选中的路径列表

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self.scene)
        self.row_height = 200  # 固定行高
        self.margin = 5  # 图片间距
        self.show_filename = False  # 是否显示文件名
        self.selected_path = None  # 当前选中的图片路径
        self.selected_paths = []  # 多选选中的图片路径列表
        self.image_items = {}  # 缓存图片项：{path: (pixmap_item, text_item)}
        self.selected_borders = {}  # 选中的边框：{path: border_item}
        self.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
        self.setRenderHint(QtGui.QPainter.Antialiasing)
        self.image_paths = []
        self.setFocusPolicy(QtCore.Qt.StrongFocus)  # 确保能接收键盘事件

    def load_images(self, image_paths, show_filename=False):
        self.image_paths = image_paths
        self.show_filename = show_filename
        self.scene.clear()
        self.image_items.clear()
        self.selected_borders.clear()
        self.selected_paths.clear()
        self.selected_path = None
        x, y = self.margin, self.margin
        max_width = self.viewport().width() - self.margin * 2
        font = QtGui.QFont()
        font.setPointSize(10)
        fm = QtGui.QFontMetrics(font)
        text_height = fm.height() if show_filename else 0

        for path in image_paths:
            try:
                img = Image.open(path)
                ratio = self.row_height / img.height
                w = int(img.width * ratio)
                h = self.row_height
                pixmap = QtGui.QPixmap(path).scaled(w, h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                item = QtWidgets.QGraphicsPixmapItem(pixmap)
                item.setData(QtCore.Qt.UserRole, path)
                if x + w > max_width:
                    x = self.margin
                    y += h + self.margin + text_height
                item.setPos(x, y)
                self.scene.addItem(item)
                text_item = None
                if show_filename:
                    text_item = QtWidgets.QGraphicsTextItem(os.path.basename(path))
                    text_item.setFont(font)
                    text_width = fm.width(os.path.basename(path))
                    text_item.setPos(x + (w - text_width) / 2, y + h)
                    self.scene.addItem(text_item)
                self.image_items[path] = (item, text_item)
                x += w + self.margin
            except Exception:
                icon = self.style().standardIcon(QtWidgets.QStyle.SP_FileIcon)
                pixmap = icon.pixmap(QtCore.QSize(self.row_height, self.row_height))
                item = QtWidgets.QGraphicsPixmapItem(pixmap)
                item.setData(QtCore.Qt.UserRole, path)
                if x + self.row_height > max_width:
                    x = self.margin
                    y += self.row_height + self.margin + text_height
                item.setPos(x, y)
                self.scene.addItem(item)
                text_item = None
                if show_filename:
                    text_item = QtWidgets.QGraphicsTextItem(os.path.basename(path))
                    text_item.setFont(font)
                    text_width = fm.width(os.path.basename(path))
                    text_item.setPos(x + (self.row_height - text_width) / 2, y + self.row_height)
                    self.scene.addItem(text_item)
                self.image_items[path] = (item, text_item)
                x += self.row_height + self.margin

        # 更新场景大小
        self.scene.setSceneRect(0, 0, max_width, y + self.row_height + text_height + self.margin)

    def update_selection_border(self, path, selected=True):
        """更新选中图片的边框"""
        if selected:
            if path in self.image_items and path not in self.selected_borders:
                item, _ = self.image_items[path]
                rect = item.boundingRect()
                x, y = item.pos().x(), item.pos().y()
                w, h = rect.width(), rect.height()
                border = QtWidgets.QGraphicsRectItem(x - 2, y - 2, w + 4, h + 4)
                border.setPen(QtGui.QPen(QtGui.QColor("blue"), 2))
                border.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
                border.setZValue(-1)
                self.scene.addItem(border)
                self.selected_borders[path] = border
        else:
            if path in self.selected_borders:
                self.scene.removeItem(self.selected_borders[path])
                del self.selected_borders[path]

    def clear_all_selections(self):
        """清除所有选择"""
        for path in list(self.selected_borders.keys()):
            self.update_selection_border(path, selected=False)
        self.selected_paths.clear()
        self.selected_path = None
        self.selectionChanged.emit(self.selected_paths.copy())

    def mousePressEvent(self, event):
        item = self.itemAt(event.pos())
        if item and isinstance(item, QtWidgets.QGraphicsPixmapItem):
            path = item.data(QtCore.Qt.UserRole)
            if path:
                modifiers = QtWidgets.QApplication.keyboardModifiers()
                
                if modifiers & QtCore.Qt.ControlModifier:
                    # CTRL键按下，切换选择状态
                    if path in self.selected_paths:
                        self.selected_paths.remove(path)
                        self.update_selection_border(path, selected=False)
                        if self.selected_path == path:
                            self.selected_path = self.selected_paths[-1] if self.selected_paths else None
                    else:
                        self.selected_paths.append(path)
                        self.update_selection_border(path, selected=True)
                        self.selected_path = path
                else:
                    # 普通点击，清除之前的选择，选择当前项
                    self.clear_all_selections()
                    self.selected_paths.append(path)
                    self.update_selection_border(path, selected=True)
                    self.selected_path = path
                    self.itemClicked.emit(path)
                
                self.selectionChanged.emit(self.selected_paths.copy())
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        item = self.itemAt(event.pos())
        if item and isinstance(item, QtWidgets.QGraphicsPixmapItem):
            path = item.data(QtCore.Qt.UserRole)
            if path:
                self.itemDoubleClicked.emit(path)
        super().mouseDoubleClickEvent(event)
    
    def keyPressEvent(self, event):
        """方向键切换选中图片 + 回车打开"""
        if not self.image_paths:
            return

        # 如果还没有选中图片，则默认选中第一张
        if not self.selected_path and self.image_paths:
            self.selected_path = self.image_paths[0]
            self.selected_paths.append(self.selected_path)
            self.update_selection_border(self.selected_path, selected=True)
            self.itemClicked.emit(self.selected_path)
            self.selectionChanged.emit(self.selected_paths.copy())
            return

        try:
            idx = self.image_paths.index(self.selected_path)
        except ValueError:
            idx = 0

        key = event.key()
        next_idx = None

        # 👉 方向键左右移动
        if key == QtCore.Qt.Key_Right:
            if idx < len(self.image_paths) - 1:
                next_idx = idx + 1
        elif key == QtCore.Qt.Key_Left:
            if idx > 0:
                next_idx = idx - 1

        # 👉 向下移动（跨行）
        elif key == QtCore.Qt.Key_Down:
            cur_item, _ = self.image_items[self.selected_path]
            cur_y = cur_item.pos().y()
            candidates = [(p, i) for i, p in enumerate(self.image_paths)
                          if self.image_items[p][0].pos().y() > cur_y + 5]
            if candidates:
                next_p, next_i = min(
                    candidates,
                    key=lambda t: (
                        self.image_items[t[0]][0].pos().y(),
                        abs(self.image_items[t[0]][0].pos().x() -
                            cur_item.pos().x())
                    )
                )
                next_idx = next_i

        # 👉 向上移动（跨行）
        elif key == QtCore.Qt.Key_Up:
            cur_item, _ = self.image_items[self.selected_path]
            cur_y = cur_item.pos().y()
            candidates = [(p, i) for i, p in enumerate(self.image_paths)
                          if self.image_items[p][0].pos().y() < cur_y - 5]
            if candidates:
                next_p, next_i = max(
                    candidates,
                    key=lambda t: (
                        self.image_items[t[0]][0].pos().y(),
                        -abs(self.image_items[t[0]][0].pos().x() -
                             cur_item.pos().x())
                    )
                )
                next_idx = next_i

        # 👉 按下 Enter / Return 键时，打开图片（等价双击）
        elif key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if self.selected_path:
                self.itemDoubleClicked.emit(self.selected_path)
            return

        # 👉 按下 SPACE 键时，传递给主窗口处理（进入标签输入）
        elif key == QtCore.Qt.Key_Space:
            # 冒泡事件到父窗口（MainWindow）
            event_ignore = QKeyEvent(event.type(), event.key(), event.modifiers())
            QtWidgets.QApplication.postEvent(self.parent().parent().parent(), event_ignore)  # 调整到 MainWindow
            return

        # 👉 按下 ESC 键时，清除所有选择
        elif key == QtCore.Qt.Key_Escape:
            self.clear_all_selections()
            return

        # 若找到了下一个图片，切换选中状态
        if next_idx is not None:
            next_path = self.image_paths[next_idx]
            
            modifiers = QtWidgets.QApplication.keyboardModifiers()
            if not (modifiers & QtCore.Qt.ControlModifier):
                # 如果没有按CTRL键，清除之前的选择
                self.clear_all_selections()
            
            self.selected_path = next_path
            if next_path not in self.selected_paths:
                self.selected_paths.append(next_path)
                self.update_selection_border(next_path, selected=True)
            self.itemClicked.emit(next_path)
            self.selectionChanged.emit(self.selected_paths.copy())

            # 自动滚动到可见区域
            item, _ = self.image_items[next_path]
            rect = item.sceneBoundingRect()
            self.ensureVisible(rect, 50, 50)
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event):
        """窗口大小改变时重新布局"""
        super().resizeEvent(event)
        if self.image_paths:
            self.load_images(self.image_paths, self.show_filename)

def find_xmp_bytes(file_path: str):
    """在文件中查找 XMP 数据块（返回 bytes 或 None）"""
    try:
        file_path = str(Path(file_path).resolve())
        with open(file_path, 'rb') as f:
            data = f.read()
        start_tag = b'<x:xmpmeta'
        end_tag = b'</x:xmpmeta>'
        i = data.find(start_tag)
        if i == -1:
            return None
        j = data.find(end_tag, i)
        if j == -1:
            return None
        j += len(end_tag)
        return data[i:j]
    except Exception:
        return None

def parse_xmp(xmp_bytes: bytes):
    """解析 XMP 字节，返回 dict 包含 title, description, keywords(list), date（str）"""
    result = {'title': '', 'description': '', 'keywords': [], 'date': ''}
    if not xmp_bytes:
        return result
    try:
        try:
            text = xmp_bytes.decode('utf-8')
        except UnicodeDecodeError:
            try:
                text = xmp_bytes.decode('utf-16')
            except Exception:
                text = xmp_bytes.decode('utf-8', errors='ignore')
        root = ET.fromstring(text)
        ns = {}
        for k, v in root.attrib.items():
            if k.startswith('xmlns:'):
                ns[k.split(':', 1)[1]] = v
        ns.setdefault('dc', 'http://purl.org/dc/elements/1.1/')
        ns.setdefault('xmp', 'http://ns.adobe.com/xap/1.0/')
        ns.setdefault('photoshop', 'http://ns.adobe.com/photoshop/1.0/')
        ns.setdefault('rdf', 'http://www.w3.org/1999/02/22-rdf-syntax-ns#')

        title = ''
        for elem in root.findall('.//{http://purl.org/dc/elements/1.1/}title'):
            li = elem.find('.//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li')
            if li is not None and li.text:
                title = li.text
                break
        result['title'] = title or ''

        desc = ''
        for elem in root.findall('.//{http://purl.org/dc/elements/1.1/}description'):
            li = elem.find('.//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li')
            if li is not None and li.text:
                desc = li.text
                break
        result['description'] = desc or ''

        keywords = []
        for elem in root.findall('.//{http://purl.org/dc/elements/1.1/}subject'):
            for li in elem.findall('.//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li'):
                if li.text:
                    keywords.append(li.text)
        if not keywords:
            for elem in root.findall('.//{http://ns.adobe.com/photoshop/1.0/}Keywords'):
                if elem.text:
                    parts = [p.strip() for p in elem.text.split(',') if p.strip()]
                    keywords.extend(parts)
        result['keywords'] = keywords

        date_val = ''
        xmp_create = root.find('.//{http://ns.adobe.com/xap/1.0/}CreateDate')
        if xmp_create is not None and xmp_create.text:
            date_val = xmp_create.text
        if not date_val:
            ps_date = root.find('.//{http://ns.adobe.com/photoshop/1.0/}DateCreated')
            if ps_date is not None and ps_date.text:
                date_val = ps_date.text
        result['date'] = date_val or ''
    except Exception:
        pass
    return result

def build_xmp_xml(title, description, keywords, date):
    """生成一个简单的 XMP 包（字符串），使用 rdf、dc、xmp、photoshop 常见元素"""
    NS = {
        'x': 'adobe:ns:meta/',
        'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
        'dc': 'http://purl.org/dc/elements/1.1/',
        'xmp': 'http://ns.adobe.com/xap/1.0/',
        'photoshop': 'http://ns.adobe.com/photoshop/1.0/',
    }
    ET.register_namespace('x', NS['x'])
    ET.register_namespace('rdf', NS['rdf'])
    ET.register_namespace('dc', NS['dc'])
    ET.register_namespace('xmp', NS['xmp'])
    ET.register_namespace('photoshop', NS['photoshop'])

    xmpmeta = ET.Element('{adobe:ns:meta/}xmpmeta')
    rdf = ET.SubElement(xmpmeta, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF')
    desc = ET.SubElement(rdf, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description')

    if title is not None:
        dc_title = ET.SubElement(desc, '{http://purl.org/dc/elements/1.1/}title')
        alt = ET.SubElement(dc_title, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Alt')
        li = ET.SubElement(alt, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li')
        li.text = title

    if description is not None:
        dc_desc = ET.SubElement(desc, '{http://purl.org/dc/elements/1.1/}description')
        alt = ET.SubElement(dc_desc, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Alt')
        li = ET.SubElement(alt, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li')
        li.text = description

    if keywords:
        dc_subject = ET.SubElement(desc, '{http://purl.org/dc/elements/1.1/}subject')
        bag = ET.SubElement(dc_subject, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Bag')
        for kw in set(keywords):  # 去重关键字
            li = ET.SubElement(bag, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li')
            li.text = kw

    if date:
        cd = ET.SubElement(desc, '{http://ns.adobe.com/xap/1.0/}CreateDate')
        cd.text = date

    if keywords:
        ps_kw = ET.SubElement(desc, '{http://ns.adobe.com/photoshop/1.0/}Keywords')
        ps_kw.text = ', '.join(set(keywords))  # 去重关键字

    xml_bytes = ET.tostring(xmpmeta, encoding='utf-8', method='xml')
    header = b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    footer = b'\n<?xpacket end="w"?>'
    return header + xml_bytes + footer

def replace_xmp_in_file(file_path: str, new_xmp_bytes: bytes):
    """将 XMP 数据 ULONG写入图片文件"""
    try:
        file_path = str(Path(file_path).resolve())
        img = Image.open(file_path)
        ext = os.path.splitext(file_path)[1].lower()
        bak = file_path + '.bak' + ext
        if not os.path.exists(bak):
            format_map = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.tif': 'TIFF', '.tiff': 'TIFF'}
            save_format = format_map.get(ext, 'JPEG')
            img.save(bak, format=save_format)
        if img.format not in ('JPEG', 'TIFF'):
            return False, f"格式 {img.format} 不支持嵌入 XMP 元数据"
        img.info['xmp'] = new_xmp_bytes
        img.save(file_path, format=img.format, exif=img.info.get('exif', b''), xmp=new_xmp_bytes)
        return True, "已将 XMP 写入图片文件"
    except Exception as e:
        return False, f"写入 XMP 失败: {str(e)}"

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("图片标签编辑器 - 支持CTRL多选")
        self.resize(1200, 700)

        # 读取 config.ini 中的标签列表
        self.load_tag_list()

        splitter = QtWidgets.QSplitter()
        self.setCentralWidget(splitter)

        # Left: Folder tree
        self.model = QtWidgets.QFileSystemModel()
        self.model.setRootPath(QtCore.QDir.rootPath())
        self.model.setFilter(QtCore.QDir.AllDirs | QtCore.QDir.NoDotAndDotDot)
        self.tree = QtWidgets.QTreeView()
        self.tree.setModel(self.model)
        for i in range(1, self.model.columnCount()):
            self.tree.hideColumn(i)
        self.tree.clicked.connect(self.on_tree_clicked)
        splitter.addWidget(self.tree)

        # Middle: Masonry view with checkbox and slider top-right
        mid_widget = QtWidgets.QWidget()
        mid_layout = QtWidgets.QVBoxLayout(mid_widget)
        mid_layout.setContentsMargins(6, 6, 6, 6)
        topbar = QtWidgets.QHBoxLayout()
        topbar.addStretch()
        self.show_filename_cb = QtWidgets.QCheckBox("显示文件名")
        self.show_filename_cb.setChecked(False)
        self.show_filename_cb.stateChanged.connect(self.on_show_filename_changed)
        topbar.addWidget(self.show_filename_cb)
        self.size_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.size_slider.setMinimum(64)
        self.size_slider.setMaximum(256)
        self.size_slider.setValue(200)  # 默认行高 200
        self.size_slider.setFixedWidth(200)
        self.size_slider.valueChanged.connect(self.on_slider_changed)
        topbar.addWidget(QtWidgets.QLabel("缩略图高度"))
        topbar.addWidget(self.size_slider)
        mid_layout.addLayout(topbar)

        # 添加多选提示标签
        self.multi_select_label = QtWidgets.QLabel("提示：按住CTRL键可多选图片")
        self.multi_select_label.setStyleSheet("color: #666; font-size: 10px;")
        mid_layout.addWidget(self.multi_select_label)

        self.masonry_view = MasonryView(mid_widget)  # 指定父级以便事件冒泡
        self.masonry_view.itemClicked.connect(self.on_thumb_selected)
        self.masonry_view.itemDoubleClicked.connect(self.open_image_preview)
        self.masonry_view.selectionChanged.connect(self.on_selection_changed)
        mid_layout.addWidget(self.masonry_view)
        splitter.addWidget(mid_widget)

        # Right: metadata editor
        right_widget = QtWidgets.QWidget()
        right_widget.setMaximumWidth(380)
        right_layout = QtWidgets.QVBoxLayout(right_widget)

        # Status label
        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.status.setFixedHeight(40)
        right_layout.addWidget(self.status)

        # 添加多选信息显示
        self.selection_info = QtWidgets.QLabel("已选择: 0 张图片")
        self.selection_info.setStyleSheet("color: #0066cc; font-weight: bold;")
        right_layout.addWidget(self.selection_info)

        # Metadata fields with labels and inputs on separate lines
        form_layout = QtWidgets.QVBoxLayout()

        # Title
        title_label = QtWidgets.QLabel("标题（Title）")
        title_label.setAlignment(QtCore.Qt.AlignLeft)
        form_layout.addWidget(title_label)
        self.title_edit = QtWidgets.QLineEdit()
        self.title_edit.setMinimumWidth(300)
        form_layout.addWidget(self.title_edit)

        # Date
        date_label = QtWidgets.QLabel("日期（Date）")
        date_label.setAlignment(QtCore.Qt.AlignLeft)
        form_layout.addWidget(date_label)
        self.date_edit = QtWidgets.QLineEdit()
        self.date_edit.setPlaceholderText("例如：2023-01-02T15:04:05")
        self.date_edit.setMinimumWidth(300)
        form_layout.addWidget(self.date_edit)

        # Description
        desc_label = QtWidgets.QLabel("描述（Description）")
        desc_label.setAlignment(QtCore.Qt.AlignLeft)
        form_layout.addWidget(desc_label)
        self.desc_edit = QtWidgets.QTextEdit()
        self.desc_edit.setMinimumWidth(300)
        self.desc_edit.setMinimumHeight(100)
        form_layout.addWidget(self.desc_edit)

        right_layout.addLayout(form_layout)

        # Keywords
        kw_layout = QtWidgets.QVBoxLayout()
        kw_label = QtWidgets.QLabel("关键字（每行一个）")
        kw_label.setAlignment(QtCore.Qt.AlignLeft)
        kw_layout.addWidget(kw_label)
        self.kw_list = QtWidgets.QPlainTextEdit()
        self.kw_list.setMinimumWidth(300)
        self.kw_list.setMinimumHeight(100)
        kw_layout.addWidget(self.kw_list)
        right_layout.addLayout(kw_layout)

        # Tag input (auto-complete)
        tag_input_layout = QtWidgets.QHBoxLayout()
        self.tag_input = QtWidgets.QLineEdit()
        self.tag_input.setPlaceholderText("输入标签（支持自动补全）")
        self.tag_input.setMinimumWidth(220)
        self.tag_input.textEdited.connect(self.on_tag_input_changed)
        self.tag_input.returnPressed.connect(self.on_add_tag_clicked)
        tag_input_layout.addWidget(self.tag_input)

        self.add_tag_btn = QtWidgets.QPushButton("确定")
        self.add_tag_btn.clicked.connect(self.on_add_tag_clicked)
        tag_input_layout.addWidget(self.add_tag_btn)
        right_layout.addLayout(tag_input_layout)

        # Save button
        btn_layout = QtWidgets.QHBoxLayout()
        self.save_btn = QtWidgets.QPushButton("保存到图片")
        self.save_btn.clicked.connect(self.on_save_clicked)
        btn_layout.addWidget(self.save_btn)
        
        # 添加批量保存按钮
        self.batch_save_btn = QtWidgets.QPushButton("批量保存")
        self.batch_save_btn.clicked.connect(self.on_batch_save_clicked)
        btn_layout.addWidget(self.batch_save_btn)
        
        btn_layout.addStretch()
        right_layout.addLayout(btn_layout)

        right_layout.addStretch()

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([250, 700, 350])

        self.current_folder = None
        self.image_paths = []
        self.selected_image_paths = []  # 存储多选的路径

        # 新增：键盘支持初始化
        self.setFocusPolicy(QtCore.Qt.StrongFocus)  # 确保主窗口能接收键盘事件
        self.tag_input_focused = False
        self.tag_input.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj == self.tag_input:
            if event.type() == QtCore.QEvent.KeyPress:
                key = event.key()
                modifiers = event.modifiers()
                if key in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down, QtCore.Qt.Key_Left, QtCore.Qt.Key_Right) and modifiers == QtCore.Qt.ControlModifier:
                    # Ctrl + 方向键：移动图片选择，但不丢失焦点
                    self.handle_ctrl_arrow_in_tag_input(key)
                    return True
                elif key == QtCore.Qt.Key_Escape:
                    self.masonry_view.setFocus()
                    return True
            elif event.type() == QtCore.QEvent.FocusIn:
                self.tag_input_focused = True
            elif event.type() == QtCore.QEvent.FocusOut:
                self.tag_input_focused = False
        return super().eventFilter(obj, event)

    def handle_ctrl_arrow_in_tag_input(self, key):
        """在标签输入框焦点下，按 Ctrl+方向键切换图片选择"""
        if not self.masonry_view.selected_path or not self.masonry_view.image_paths:
            return
        try:
            idx = self.masonry_view.image_paths.index(self.masonry_view.selected_path)
        except ValueError:
            idx = 0
        next_idx = idx
        if key == QtCore.Qt.Key_Right:
            if idx < len(self.masonry_view.image_paths) - 1:
                next_idx = idx + 1
        elif key == QtCore.Qt.Key_Left:
            if idx > 0:
                next_idx = idx - 1
        elif key == QtCore.Qt.Key_Down:
            # 复用 MasonryView 的 Down 逻辑（跨行）
            cur_item, _ = self.masonry_view.image_items[self.masonry_view.selected_path]
            cur_y = cur_item.pos().y()
            candidates = [(p, i) for i, p in enumerate(self.masonry_view.image_paths)
                          if self.masonry_view.image_items[p][0].pos().y() > cur_y + 5]
            if candidates:
                next_p, next_i = min(
                    candidates,
                    key=lambda t: (
                        self.masonry_view.image_items[t[0]][0].pos().y(),
                        abs(self.masonry_view.image_items[t[0]][0].pos().x() - cur_item.pos().x())
                    )
                )
                next_idx = next_i
        elif key == QtCore.Qt.Key_Up:
            # 复用 Up 逻辑
            cur_item, _ = self.masonry_view.image_items[self.masonry_view.selected_path]
            cur_y = cur_item.pos().y()
            candidates = [(p, i) for i, p in enumerate(self.masonry_view.image_paths)
                          if self.masonry_view.image_items[p][0].pos().y() < cur_y - 5]
            if candidates:
                next_p, next_i = max(
                    candidates,
                    key=lambda t: (
                        self.masonry_view.image_items[t[0]][0].pos().y(),
                        -abs(self.masonry_view.image_items[t[0]][0].pos().x() - cur_item.pos().x())
                    )
                )
                next_idx = next_i
        if next_idx != idx:
            next_path = self.masonry_view.image_paths[next_idx]
            self.masonry_view.selected_path = next_path
            if next_path not in self.masonry_view.selected_paths:
                self.masonry_view.selected_paths.append(next_path)
                self.masonry_view.update_selection_border(next_path, selected=True)
            self.masonry_view.itemClicked.emit(next_path)
            self.on_thumb_selected(next_path)  # 更新右侧元数据
            # 滚动可见
            item, _ = self.masonry_view.image_items[next_path]
            rect = item.sceneBoundingRect()
            self.masonry_view.ensureVisible(rect, 50, 50)

    def keyPressEvent(self, event):
        """主窗口键盘事件：SPACE 进入标签输入，ESC 从输入框返回（但 ESC 已移到 filter）"""
        if event.key() == QtCore.Qt.Key_Space and self.masonry_view.hasFocus() and self.masonry_view.selected_path:
            self.tag_input.setFocus()
            event.accept()
            return
        super().keyPressEvent(event)

    def load_tag_list(self):
        """读取 config.ini 中的 [taglist] 节"""
        self.config_path = Path("config.ini")
        self.tag_list = []
        self.config = configparser.ConfigParser(strict=False, allow_no_value=True)
        self.config.optionxform = str 
        if self.config_path.exists():
            self.config.read(self.config_path, encoding="utf-8")
            if 'taglist' in self.config:
                self.tag_list = [k.strip() for k in self.config['taglist'] if k.strip()]
        else:
            self.config['taglist'] = {}
            with open(self.config_path, "w", encoding="utf-8") as f:
                self.config.write(f)

    def on_tag_input_changed(self, text):
        if not text:
            return
        for tag in self.tag_list:
            if tag.lower().startswith(text.lower()):
                cursor_pos = self.tag_input.cursorPosition()
                self.tag_input.blockSignals(True)
                self.tag_input.setText(tag)
                self.tag_input.setSelection(cursor_pos, len(tag) - cursor_pos)
                self.tag_input.blockSignals(False)
                return
        # 没匹配则不动

    def on_add_tag_clicked(self):
        """将输入框的标签追加到关键字框，并更新 config.ini"""
        new_tag = self.tag_input.text().strip()
        if not new_tag:
            return
        # 获取现有关键字（忽略大小写去重）
        existing = set(k.strip().lower() for k in self.kw_list.toPlainText().splitlines() if k.strip())
        if new_tag.lower() not in existing:
            self.kw_list.appendPlainText(new_tag)
            # 如果标签不在 tag_list，追加并写入 config.ini
            if new_tag not in self.tag_list:
                self.tag_list.append(new_tag)
                self.config.set('taglist', new_tag, None)
                with open(self.config_path, "w", encoding="utf-8") as f:
                    self.config.write(f)
        self.tag_input.clear()

    def on_tree_clicked(self, index):
        path = self.model.filePath(index)
        if os.path.isdir(path):
            self.load_folder_images(path)

    def load_folder_images(self, folder_path):
        self.current_folder = folder_path
        self.image_paths = []
        try:
            folder_path = str(Path(folder_path).resolve())
            entries = sorted(os.listdir(folder_path), key=lambda s: s.lower())
        except Exception:
            entries = []
        for name in entries:
            full = os.path.join(folder_path, name)
            if os.path.isfile(full) and os.path.splitext(name)[1].lower() in IMAGE_EXTS:
                self.image_paths.append(full)
        self.masonry_view.load_images(self.image_paths, self.show_filename_cb.isChecked())

    def on_slider_changed(self, value):
        self.masonry_view.row_height = value
        if self.current_folder:
            self.load_folder_images(self.current_folder)

    def on_show_filename_changed(self, state):
        if self.current_folder:
            self.load_folder_images(self.current_folder)

    def on_thumb_selected(self, path):
        self.load_metadata_into_ui(path)

    def on_selection_changed(self, selected_paths):
        """处理多选变化"""
        self.selected_image_paths = selected_paths
        self.selection_info.setText(f"已选择: {len(selected_paths)} 张图片")
        
        # 如果有选中的图片，显示最后选中的图片的元数据
        if selected_paths:
            last_selected = selected_paths[-1]
            self.load_metadata_into_ui(last_selected)

    def open_image_preview(self, path):
        path = str(Path(path).resolve())
        if sys.platform.startswith('darwin'):
            os.system(f'open "{path}"')
        elif os.name == 'nt':
            os.startfile(path)
        else:
            os.system(f'xdg-open "{path}"')

    def load_metadata_into_ui(self, image_path):
        self.status.setText(f"读取: {image_path}")
        xmp_bytes = find_xmp_bytes(image_path)
        meta = {'title': '', 'description': '', 'keywords': [], 'date': ''}
        if xmp_bytes:
            meta = parse_xmp(xmp_bytes)
            self.status.setText("已读取 XMP 元数据（优先）")
        else:
            if HAS_IPTC and image_path.lower().endswith(('.jpg', '.jpeg')):
                try:
                    info = IPTCInfo(image_path, force=True)
                    title = info.get('object name') or ''
                    caption = info.get('caption/abstract') or ''
                    keywords = info.get('keywords') or []
                    date_created = info.get('date created') or ''
                    meta = {
                        'title': title if isinstance(title, str) else str(title),
                        'description': caption if isinstance(caption, str) else str(caption),
                        'keywords': [kw.decode('utf-8') if isinstance(kw, bytes) else str(kw) for kw in keywords],
                        'date': date_created if isinstance(date_created, str) else str(date_created)
                    }
                    self.status.setText("已读取 IPTC 元数据")
                except Exception:
                    pass
        self.title_edit.setText(meta.get('title', '') or '')
        self.date_edit.setText(meta.get('date', '') or '')
        self.desc_edit.setPlainText(meta.get('description', '') or '')
        kws = meta.get('keywords', []) or []
        self.kw_list.setPlainText("\n".join(kws))
        self.current_image = image_path

    def on_save_clicked(self):
        if not hasattr(self, 'current_image') or not self.current_image:
            self.status.setText("请先选择一张图片再保存。")
            return
        title = self.title_edit.text().strip()
        date = self.date_edit.text().strip()
        desc = self.desc_edit.toPlainText().strip()
        kws_raw = self.kw_list.toPlainText().splitlines()
        keywords = [k.strip() for k in kws_raw if k.strip()]
        new_xmp = build_xmp_xml(title, desc, keywords, date)
        success, message = replace_xmp_in_file(self.current_image, new_xmp)
        if success:
            self.status.setText("保存成功: " + message)
        else:
            self.status.setText("保存失败: " + message)

    def on_batch_save_clicked(self):
        """批量保存功能"""
        if not self.selected_image_paths:
            self.status.setText("请先选择要批量保存的图片")
            return
        
        title = self.title_edit.text().strip()
        date = self.date_edit.text().strip()
        desc = self.desc_edit.toPlainText().strip()
        kws_raw = self.kw_list.toPlainText().splitlines()
        keywords = [k.strip() for k in kws_raw if k.strip()]
        new_xmp = build_xmp_xml(title, desc, keywords, date)
        
        success_count = 0
        fail_count = 0
        
        for image_path in self.selected_image_paths:
            success, message = replace_xmp_in_file(image_path, new_xmp)
            if success:
                success_count += 1
            else:
                fail_count += 1
        
        self.status.setText(f"批量保存完成: 成功 {success_count} 张, 失败 {fail_count} 张")

def main():
    app = QtWidgets.QApplication(sys.argv)
    wnd = MainWindow()
    wnd.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
