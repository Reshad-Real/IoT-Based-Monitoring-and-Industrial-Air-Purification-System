"""
Air Quality Monitoring Dashboard with SQLite Database
Real-time visualization and data logging from ESP32 mesh network
Enhanced with General User Dashboard
"""

import sys
import serial
import json
import sqlite3
from collections import deque
from datetime import datetime
import numpy as np
from pathlib import Path

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QComboBox, QPushButton, 
                             QGroupBox, QGridLayout, QTabWidget, QProgressBar,
                             QScrollArea, QTableWidget, QTableWidgetItem, QHeaderView,
                             QFileDialog, QFrame, QStackedWidget)
from PyQt5.QtCore import QTimer, Qt, pyqtSignal, QThread, QRectF, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QFont, QPalette, QColor, QPainter, QConicalGradient, QRadialGradient, QLinearGradient, QPainterPath

import pyqtgraph as pg
from pyqtgraph import PlotWidget, BarGraphItem, TextItem

# Configure PyQtGraph
pg.setConfigOption('background', '#1e1e1e')
pg.setConfigOption('foreground', 'w')


class DatabaseManager:
    """Manages SQLite database operations for sensor data"""
    
    def __init__(self, db_path='air_quality_data.db'):
        """Initialize database connection and create tables"""
        self.db_path = db_path
        self.conn = None
        self.cursor = None
        self.init_database()
        
    def init_database(self):
        """Create database and tables if they don't exist"""
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.cursor = self.conn.cursor()
            
            # Drop existing table to start fresh
            self.cursor.execute('DROP TABLE IF EXISTS sensor_data')
            
            # Create main sensor data table
            self.cursor.execute('''
                CREATE TABLE sensor_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    node_id INTEGER NOT NULL,
                    node_type TEXT,
                    pm25 REAL,
                    pm10 REAL,
                    eco2 REAL,
                    tvoc REAL,
                    co REAL,
                    no2 REAL,
                    temperature REAL,
                    humidity REAL,
                    data_quality TEXT DEFAULT 'GOOD'
                )
            ''')
            
            # Create index for faster queries
            self.cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_timestamp 
                ON sensor_data(timestamp)
            ''')
            
            self.cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_node_id 
                ON sensor_data(node_id)
            ''')
            
            # Create summary statistics table
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_statistics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    node_id INTEGER NOT NULL,
                    avg_pm25 REAL,
                    max_pm25 REAL,
                    min_pm25 REAL,
                    avg_eco2 REAL,
                    avg_temp REAL,
                    total_readings INTEGER,
                    UNIQUE(date, node_id)
                )
            ''')
            
            # Insert some sample data for testing
            self.insert_sample_data()
            
            self.conn.commit()
            print(f"Database initialized (fresh): {self.db_path}")
            
        except sqlite3.Error as e:
            print(f"Database error: {e}")
    
    def insert_sample_data(self):
        """Insert sample data for testing"""
        import random
        from datetime import datetime, timedelta
        
        print("Inserting sample data...")
        now = datetime.now()
        
        for i in range(100):  # Insert 100 sample records
            timestamp = now - timedelta(minutes=i*10)
            for node_id in [1, 2, 3]:
                data = {
                    'nodeType': 'ESP32-S3',
                    'pm25': random.uniform(10, 150),
                    'pm10': random.uniform(20, 200),
                    'eco2': random.uniform(400, 2000),
                    'tvoc': random.uniform(100, 10000),
                    'co': random.uniform(1, 1000),
                    'no2': random.uniform(10, 1000),
                    'temp': random.uniform(20, 30),
                    'humidity': random.uniform(40, 70)
                }
                
                # Format timestamp for SQLite
                formatted_time = timestamp.strftime('%Y-%m-%d %H:%M:%S')
                
                self.cursor.execute('''
                    INSERT INTO sensor_data 
                    (timestamp, node_id, node_type, pm25, pm10, eco2, tvoc, co, no2, temperature, humidity)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    formatted_time,
                    node_id,
                    data.get('nodeType', 'Unknown'),
                    data.get('pm25', None),
                    data.get('pm10', None),
                    data.get('eco2', None),
                    data.get('tvoc', None),
                    data.get('co', None),
                    data.get('no2', None),
                    data.get('temp', None),
                    data.get('humidity', None)
                ))
        
        self.conn.commit()
        print("Sample data inserted successfully")
            
    def validate_data(self, value, min_val, max_val):
        """Validate data is within reasonable range"""
        if value is None:
            return None
        try:
            val = float(value)
            if min_val <= val <= max_val:
                return val
            return None
        except (ValueError, TypeError):
            return None
            
    def insert_sensor_data(self, node_id, data):
        """Insert sensor reading into database with validation"""
        try:
            # Validate all data before inserting
            pm25 = self.validate_data(data.get('pm25'), 0, 10000)
            pm10 = self.validate_data(data.get('pm10'), 0, 10000)
            eco2 = self.validate_data(data.get('eco2'), 0, 10000)
            tvoc = self.validate_data(data.get('tvoc'), 0, 10000)
            co = self.validate_data(data.get('co'), 0, 10000)
            no2 = self.validate_data(data.get('no2'), 0, 10000)
            temp = self.validate_data(data.get('temp'), -50, 100)
            humidity = self.validate_data(data.get('humidity'), 0, 100)
            
            self.cursor.execute('''
                INSERT INTO sensor_data 
                (node_id, node_type, pm25, pm10, eco2, tvoc, co, no2, temperature, humidity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                node_id,
                data.get('nodeType', 'Unknown'),
                pm25, pm10, eco2, tvoc, co, no2, temp, humidity
            ))
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"Insert error: {e}")
            return False
            
    def get_recent_readings(self, limit=50):
        """Get most recent sensor readings"""
        try:
            self.cursor.execute('''
                SELECT id, timestamp, node_id, node_type, pm25, pm10, eco2, 
                       tvoc, co, no2, temperature, humidity
                FROM sensor_data
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (limit,))
            rows = self.cursor.fetchall()
            return rows
        except sqlite3.Error as e:
            print(f"Query error: {e}")
            return []
            
    def get_statistics(self):
        """Get database statistics"""
        try:
            # Total records
            self.cursor.execute('SELECT COUNT(*) FROM sensor_data')
            total_records = self.cursor.fetchone()[0]
            
            # Records per node
            self.cursor.execute('''
                SELECT node_id, COUNT(*) 
                FROM sensor_data 
                GROUP BY node_id
            ''')
            node_counts = dict(self.cursor.fetchall())
            
            # Database size
            db_size = Path(self.db_path).stat().st_size / 1024  # KB
            
            # Date range
            self.cursor.execute('''
                SELECT MIN(timestamp), MAX(timestamp) 
                FROM sensor_data
            ''')
            date_range = self.cursor.fetchone()
            
            return {
                'total_records': total_records,
                'node_counts': node_counts,
                'db_size_kb': db_size,
                'first_record': date_range[0] if date_range and date_range[0] else None,
                'last_record': date_range[1] if date_range and date_range[1] else None
            }
        except sqlite3.Error as e:
            print(f"Statistics error: {e}")
            return None
            
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()


class CircularMeter(QWidget):
    """Circular gauge meter widget"""
    
    def __init__(self, title, unit, min_val=0, max_val=100, color='#00ff00'):
        super().__init__()
        self.title = title
        self.unit = unit
        self.min_val = min_val
        self.max_val = max_val
        self.value = 0
        self.target_value = 0
        self.color = QColor(color)
        self.setMinimumSize(180, 180)
        
        # 60 FPS animation timer
        self.anim_timer = QTimer()
        self.anim_timer.timeout.connect(self.animate_value)
        
    def setValue(self, value):
        """Set meter value with smooth transition"""
        # Validate input
        if value is None or not isinstance(value, (int, float)):
            return
        if not (self.min_val <= value <= self.max_val):
            return
            
        self.target_value = value
        if not self.anim_timer.isActive():
            self.anim_timer.start(16)  # ~60 FPS
    
    def animate_value(self):
        """Animate value change at 60 FPS"""
        diff = self.target_value - self.value
        if abs(diff) < 0.3:
            self.value = self.target_value
            self.anim_timer.stop()
        else:
            self.value += diff * 0.2
        self.update()
        
    def paintEvent(self, event):
        """Draw the circular meter"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        width = self.width()
        height = self.height()
        side = min(width, height)
        
        painter.translate(width / 2, height / 2)
        painter.scale(side / 200.0, side / 200.0)
        
        # Draw background circle
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#2d2d2d'))
        painter.drawEllipse(QRectF(-90, -90, 180, 180))
        
        # Draw progress arc
        gradient = QConicalGradient(0, 0, 0)
        gradient.setColorAt(0, self.color)
        gradient.setColorAt(1, self.color.darker(150))
        
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        
        # Calculate angle based on value
        percentage = (self.value - self.min_val) / (self.max_val - self.min_val)
        angle = int(percentage * 360 * 16)
        
        painter.drawPie(QRectF(-85, -85, 170, 170), 90 * 16, -angle)
        
        # Draw inner circle
        painter.setBrush(QColor('#1e1e1e'))
        painter.drawEllipse(QRectF(-70, -70, 140, 140))
        
        # Draw text
        painter.setPen(QColor('#ffffff'))
        painter.setFont(QFont('Arial', 24, QFont.Bold))
        painter.drawText(QRectF(-70, -20, 140, 40), Qt.AlignCenter, f"{self.value:.1f}")
        
        painter.setFont(QFont('Arial', 10))
        painter.drawText(QRectF(-70, 10, 140, 30), Qt.AlignCenter, self.unit)
        
        painter.setFont(QFont('Arial', 9))
        painter.drawText(QRectF(-70, -60, 140, 20), Qt.AlignCenter, self.title)


class HorizontalBar(QWidget):
    """Horizontal bar meter with gradient"""
    
    def __init__(self, title, unit, min_val=0, max_val=100, colors=None):
        super().__init__()
        self.title = title
        self.unit = unit
        self.min_val = min_val
        self.max_val = max_val
        self.value = 0
        self.target_value = 0
        self.colors = colors or ['#00ff00', '#ffff00', '#ff8800', '#ff0000']
        self.setMinimumHeight(80)
        
        # 60 FPS animation timer
        self.anim_timer = QTimer()
        self.anim_timer.timeout.connect(self.animate_value)
        
    def setValue(self, value):
        """Set bar value with smooth transition"""
        # Validate input
        if value is None or not isinstance(value, (int, float)):
            return
        if not (self.min_val <= value <= self.max_val):
            return
            
        self.target_value = value
        if not self.anim_timer.isActive():
            self.anim_timer.start(16)  # ~60 FPS
    
    def animate_value(self):
        """Animate value change at 60 FPS"""
        diff = self.target_value - self.value
        if abs(diff) < 0.3:
            self.value = self.target_value
            self.anim_timer.stop()
        else:
            self.value += diff * 0.2
        self.update()
        
    def getColor(self):
        """Get color based on value"""
        percentage = (self.value - self.min_val) / (self.max_val - self.min_val)
        idx = min(int(percentage * len(self.colors)), len(self.colors) - 1)
        return QColor(self.colors[idx])
        
    def paintEvent(self, event):
        """Draw the horizontal bar"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        width = self.width()
        height = self.height()
        
        # Draw title
        painter.setPen(QColor('#ffffff'))
        painter.setFont(QFont('Arial', 10, QFont.Bold))
        painter.drawText(10, 20, self.title)
        
        # Draw background bar
        bar_y = 35
        bar_height = 25
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#2d2d2d'))
        painter.drawRoundedRect(10, bar_y, width - 20, bar_height, 5, 5)
        
        # Draw value bar
        percentage = (self.value - self.min_val) / (self.max_val - self.min_val)
        bar_width = int((width - 20) * percentage)
        
        gradient = QRadialGradient(bar_width / 2, bar_y + bar_height / 2, bar_width)
        color = self.getColor()
        gradient.setColorAt(0, color.lighter(120))
        gradient.setColorAt(1, color)
        
        painter.setBrush(gradient)
        painter.drawRoundedRect(10, bar_y, bar_width, bar_height, 5, 5)
        
        # Draw value text
        painter.setPen(QColor('#ffffff'))
        painter.setFont(QFont('Arial', 12, QFont.Bold))
        text = f"{self.value:.1f} {self.unit}"
        painter.drawText(10, bar_y + bar_height + 15, text)


class VerticalGradientProgressBar(QWidget):
    """Custom vertical progress bar with gradient and black text for AQI"""
    
    def __init__(self, node_id):
        super().__init__()
        self.node_id = node_id
        self.value = 0
        self.percentage = 0
        self.target_value = 0
        self.target_percentage = 0
        self.setMinimumWidth(120)
        self.setMinimumHeight(300)
        
        # 60 FPS animation timer
        self.anim_timer = QTimer()
        self.anim_timer.timeout.connect(self.animate_value)
        
    def setValue(self, value, percentage):
        """Set bar value with smooth transition"""
        self.target_value = value
        self.target_percentage = percentage
        
        if not self.anim_timer.isActive():
            self.anim_timer.start(16)  # ~60 FPS
    
    def animate_value(self):
        """Animate value change at 60 FPS"""
        value_diff = self.target_value - self.value
        perc_diff = self.target_percentage - self.percentage
        
        if abs(value_diff) < 0.3 and abs(perc_diff) < 0.3:
            self.value = self.target_value
            self.percentage = self.target_percentage
            self.anim_timer.stop()
        else:
            self.value += value_diff * 0.2
            self.percentage += perc_diff * 0.2
            
        self.update()
        
    def getAQIColor(self, aqi_value):
        """Get color based on AQI value"""
        if aqi_value <= 50:  # Good
            return QColor('#00ff00')
        elif aqi_value <= 100:  # Moderate
            return QColor('#ffff00')
        elif aqi_value <= 150:  # Unhealthy for Sensitive Groups
            return QColor('#ff8800')
        elif aqi_value <= 200:  # Unhealthy
            return QColor('#ff0000')
        elif aqi_value <= 300:  # Very Unhealthy
            return QColor('#9900cc')
        else:  # Hazardous
            return QColor('#660099')
        
    def paintEvent(self, event):
        """Custom paint with gradient and black text"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        width = self.width()
        height = self.height()
        
        # Draw background
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#2d2d2d'))
        painter.drawRoundedRect(0, 0, width, height, 10, 10)
        
        # Calculate fill height
        fill_height = int(height * (self.percentage / 100))
        
        if fill_height > 0:
            # Create vertical gradient from bottom to top
            gradient = QLinearGradient(0, height, 0, height - fill_height)
            
            # Get AQI color based on value
            base_color = self.getAQIColor(self.value)
            
            # Create gradient with 3 color stops
            gradient.setColorAt(0, base_color.darker(150))
            gradient.setColorAt(0.5, base_color)
            gradient.setColorAt(1, base_color.lighter(150))
            
            painter.setBrush(gradient)
            painter.drawRoundedRect(
                0, height - fill_height,
                width, fill_height,
                10, 10
            )
            
            # Draw inner glow effect
            painter.setPen(QPen(base_color.lighter(200), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(
                2, height - fill_height + 2,
                width - 4, fill_height - 4,
                8, 8
            )
        
        # Draw node number at top
        painter.setPen(QColor('#ffffff'))
        painter.setFont(QFont('Arial', 16, QFont.Bold))
        painter.drawText(QRectF(0, 10, width, 30), Qt.AlignCenter, f"Node {self.node_id}")
        
        # Draw AQI value in middle of bar
        painter.setFont(QFont('Arial', 28, QFont.Bold))
        painter.setPen(QColor('#ffffff'))
        painter.drawText(QRectF(0, height/2 - 40, width, 80), Qt.AlignCenter, f"{int(self.value)}")
        
        # Draw percentage at bottom
        painter.setFont(QFont('Arial', 12, QFont.Bold))
        painter.drawText(QRectF(0, height - 40, width, 30), Qt.AlignCenter, f"{self.percentage:.1f}%")
        
        # Draw AQI label
        painter.setFont(QFont('Arial', 10))
        painter.drawText(QRectF(0, height/2 + 40, width, 30), Qt.AlignCenter, "AQI")


class DatabaseViewWidget(QWidget):
    """Widget to display real-time database updates and statistics"""
    
    def __init__(self, db_manager):
        super().__init__()
        self.db_manager = db_manager
        self.update_pending = False
        self.init_ui()
        
    def init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout()
        
        # Database statistics group
        stats_group = QGroupBox("Database Statistics")
        stats_group.setStyleSheet("QGroupBox { color: #00ff88; font-size: 14px; font-weight: bold; }")
        stats_layout = QGridLayout()
        
        # Create statistics labels
        self.stat_labels = {}
        stats = [
            ("Total Records:", "total_records"),
            ("Database Size:", "db_size"),
            ("Node 1 Records:", "node1_count"),
            ("Node 2 Records:", "node2_count"),
            ("Node 3 Records:", "node3_count"),
            ("First Record:", "first_record"),
            ("Last Record:", "last_record"),
            ("Recording Status:", "recording_status")
        ]
        
        for idx, (label_text, key) in enumerate(stats):
            label = QLabel(label_text)
            label.setStyleSheet("font-size: 12px; color: #cccccc;")
            value = QLabel("--")
            value.setStyleSheet("font-size: 12px; font-weight: bold; color: #00aaff;")
            
            row = idx // 2
            col = (idx % 2) * 2
            stats_layout.addWidget(label, row, col)
            stats_layout.addWidget(value, row, col + 1)
            
            self.stat_labels[key] = value
        
        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)
        
        # Recent readings table
        table_group = QGroupBox("Recent Database Entries (Last 20)")
        table_group.setStyleSheet("QGroupBox { color: #ffaa00; font-size: 14px; font-weight: bold; }")
        table_layout = QVBoxLayout()
        
        self.readings_table = QTableWidget()
        self.readings_table.setColumnCount(12)
        self.readings_table.setHorizontalHeaderLabels([
            'ID', 'Timestamp', 'Node', 'Type', 'PM2.5', 'PM10', 
            'eCO2', 'TVOC', 'CO', 'NO2', 'Temp', 'Humidity'
        ])
        
        # Style the table
        self.readings_table.setStyleSheet("""
            QTableWidget {
                background-color: #2d2d2d;
                gridline-color: #444444;
                color: #ffffff;
                font-size: 10px;
            }
            QHeaderView::section {
                background-color: #007acc;
                color: white;
                padding: 5px;
                border: 1px solid #444444;
                font-weight: bold;
                font-size: 11px;
            }
            QTableWidget::item {
                padding: 5px;
            }
            QTableWidget::item:alternate {
                background-color: #ffffff;
                color: #000000;
            }
        """)
        
        # Set column widths
        header = self.readings_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)
        
        self.readings_table.setAlternatingRowColors(True)
        self.readings_table.setEditTriggers(QTableWidget.NoEditTriggers)
        
        table_layout.addWidget(self.readings_table)
        
        # Export buttons
        export_layout = QHBoxLayout()
        export_layout.addStretch()
        
        csv_btn = QPushButton("📥 Export to CSV")
        csv_btn.clicked.connect(self.export_to_csv)
        export_layout.addWidget(csv_btn)
        
        excel_btn = QPushButton("📥 Export to Excel")
        excel_btn.clicked.connect(self.export_to_excel)
        export_layout.addWidget(excel_btn)
        
        table_layout.addLayout(export_layout)
        table_group.setLayout(table_layout)
        layout.addWidget(table_group)
        
        self.setLayout(layout)
        
    def update_statistics(self):
        """Update database statistics"""
        try:
            stats = self.db_manager.get_statistics()
            if stats:
                self.stat_labels['total_records'].setText(f"{stats['total_records']:,}")
                self.stat_labels['db_size'].setText(f"{stats['db_size_kb']:.1f} KB")
                
                node_counts = stats['node_counts']
                self.stat_labels['node1_count'].setText(f"{node_counts.get(1, 0):,}")
                self.stat_labels['node2_count'].setText(f"{node_counts.get(2, 0):,}")
                self.stat_labels['node3_count'].setText(f"{node_counts.get(3, 0):,}")
                
                if stats['first_record']:
                    first_record = stats['first_record']
                    if isinstance(first_record, str):
                        self.stat_labels['first_record'].setText(first_record[:19])
                    else:
                        self.stat_labels['first_record'].setText(str(first_record)[:19])
                else:
                    self.stat_labels['first_record'].setText("No data")
                    
                if stats['last_record']:
                    last_record = stats['last_record']
                    if isinstance(last_record, str):
                        self.stat_labels['last_record'].setText(last_record[:19])
                    else:
                        self.stat_labels['last_record'].setText(str(last_record)[:19])
                else:
                    self.stat_labels['last_record'].setText("No data")
                    
                self.stat_labels['recording_status'].setText("✓ ACTIVE")
                self.stat_labels['recording_status'].setStyleSheet(
                    "font-size: 12px; font-weight: bold; color: #00ff00;"
                )
        except Exception as e:
            print(f"Statistics update error: {e}")
            
    def update_table(self):
        """Update recent readings table"""
        try:
            readings = self.db_manager.get_recent_readings(20)
            
            self.readings_table.setRowCount(len(readings))
            
            for row, record in enumerate(readings):
                for col, value in enumerate(record):
                    if value is None:
                        value = "--"
                    elif isinstance(value, float):
                        value = f"{value:.2f}"
                    else:
                        value = str(value)
                        
                    item = QTableWidgetItem(value)
                    item.setTextAlignment(Qt.AlignCenter)
                    
                    # Color code by node
                    if col == 2:  # Node ID column
                        if value == "1":
                            item.setForeground(QColor('#00ff00'))
                        elif value == "2":
                            item.setForeground(QColor('#0088ff'))
                        elif value == "3":
                            item.setForeground(QColor('#ff8800'))
                            
                    self.readings_table.setItem(row, col, item)
        except Exception as e:
            print(f"Table update error: {e}")
    
    def export_to_csv(self):
        """Export database to CSV file"""
        import csv
        
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export to CSV", "air_quality_data.csv", "CSV Files (*.csv)"
        )
        
        if filename:
            try:
                readings = self.db_manager.get_recent_readings(1000)
                
                with open(filename, 'w', newline='') as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerow([
                        'ID', 'Timestamp', 'Node', 'Type', 'PM2.5', 'PM10',
                        'eCO2', 'TVOC', 'CO', 'NO2', 'Temp', 'Humidity'
                    ])
                    writer.writerows(readings)
                
                print(f"Data exported to {filename}")
            except Exception as e:
                print(f"Export error: {e}")
    
    def export_to_excel(self):
        """Export database to Excel file"""
        try:
            import openpyxl
            from openpyxl import Workbook
            
            filename, _ = QFileDialog.getSaveFileName(
                self, "Export to Excel", "air_quality_data.xlsx", "Excel Files (*.xlsx)"
            )
            
            if filename:
                readings = self.db_manager.get_recent_readings(1000)
                
                wb = Workbook()
                ws = wb.active
                ws.title = "Sensor Data"
                
                ws.append([
                    'ID', 'Timestamp', 'Node', 'Type', 'PM2.5', 'PM10',
                    'eCO2', 'TVOC', 'CO', 'NO2', 'Temp', 'Humidity'
                ])
                
                for row in readings:
                    ws.append(row)
                
                wb.save(filename)
                print(f"Data exported to {filename}")
        except ImportError:
            print("openpyxl not installed. Install with: pip install openpyxl")
        except Exception as e:
            print(f"Export error: {e}")


class SerialReader(QThread):
    """Thread for reading serial data"""
    data_received = pyqtSignal(dict)
    status_update = pyqtSignal(str)
    
    def __init__(self, port, baudrate=115200):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.running = False
        self.serial_conn = None
        
    def validate_sensor_data(self, data):
        """Validate sensor data before emitting"""
        if not isinstance(data, dict):
            return None
            
        if 'nodeId' not in data:
            return None
            
        try:
            node_id = int(data['nodeId'])
            if not (1 <= node_id <= 3):
                return None
        except (ValueError, TypeError):
            return None
            
        # Validate numeric fields
        validated = {'nodeId': node_id}
        
        if 'nodeType' in data:
            validated['nodeType'] = str(data['nodeType'])
            
        numeric_fields = {
            'pm25': (0, 10000),
            'pm10': (0, 10000),
            'eco2': (0, 10000),
            'tvoc': (0, 10000),
            'co': (0, 10000),
            'no2': (0, 10000),
            'temp': (-50, 100),
            'humidity': (0, 100)
        }
        
        for field, (min_val, max_val) in numeric_fields.items():
            if field in data:
                try:
                    val = float(data[field])
                    if min_val <= val <= max_val:
                        validated[field] = val
                except (ValueError, TypeError):
                    pass
        
        return validated if len(validated) > 1 else None
        
    def run(self):
        """Main thread loop"""
        try:
            self.serial_conn = serial.Serial(self.port, self.baudrate, timeout=1)
            self.running = True
            self.status_update.emit(f"Connected to {self.port}")
            
            while self.running:
                if self.serial_conn.in_waiting:
                    try:
                        line = self.serial_conn.readline().decode('utf-8', errors='ignore').strip()
                        
                        if '{' in line and '}' in line:
                            json_start = line.index('{')
                            json_end = line.rindex('}') + 1
                            json_str = line[json_start:json_end]
                            
                            data = json.loads(json_str)
                            
                            if 'nodes' in data:
                                for node_data in data['nodes']:
                                    validated = self.validate_sensor_data(node_data)
                                    if validated:
                                        self.data_received.emit(validated)
                            elif 'nodeId' in data:
                                validated = self.validate_sensor_data(data)
                                if validated:
                                    self.data_received.emit(validated)
                                
                    except (json.JSONDecodeError, ValueError) as e:
                        pass  # Silently ignore garbage data
                        
        except serial.SerialException as e:
            self.status_update.emit(f"Error: {str(e)}")
        finally:
            if self.serial_conn and self.serial_conn.is_open:
                self.serial_conn.close()
                
    def stop(self):
        """Stop the thread"""
        self.running = False
        self.wait()


class NodeWidget(QWidget):
    """Widget for displaying a single node's data"""
    
    def __init__(self, node_id, has_env_data=True):
        super().__init__()
        self.node_id = node_id
        self.has_env_data = has_env_data
        self.init_ui()
        
    def init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout()
        layout.setSpacing(15)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # Header
        header_layout = QHBoxLayout()
        node_label = QLabel(f"NODE {self.node_id}")
        node_label.setStyleSheet("font-size: 32px; font-weight: bold; color: #00aaff;")
        header_layout.addWidget(node_label)
        
        header_layout.addStretch()
        
        self.status_indicator = QLabel("●")
        self.status_indicator.setStyleSheet("font-size: 40px; color: #ff0000;")
        header_layout.addWidget(self.status_indicator)
        
        self.status_label = QLabel("OFFLINE")
        self.status_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #ff0000;")
        header_layout.addWidget(self.status_label)
        
        layout.addLayout(header_layout)
        
        # Node type
        self.type_label = QLabel("Type: Unknown")
        self.type_label.setStyleSheet("font-size: 14px; color: #888888; margin-bottom: 10px;")
        layout.addWidget(self.type_label)
        
        # Circular meters
        meters_group = QGroupBox("Air Quality Sensors")
        meters_group.setStyleSheet("QGroupBox { color: #00ff88; font-size: 13px; font-weight: bold; }")
        meters_layout = QGridLayout()
        meters_layout.setSpacing(20)
        self.pm25_meter = CircularMeter("PM2.5", "µg/m³", 0, 5000, '#ff6b6b')
        self.pm10_meter = CircularMeter("PM10", "µg/m³", 0, 5000, '#ff8c42')
        self.eco2_meter = CircularMeter("eCO2", "ppm", 400, 5000, '#4ecdc4')
        
        meters_layout.addWidget(self.pm25_meter, 0, 0)
        meters_layout.addWidget(self.pm10_meter, 0, 1)
        meters_layout.addWidget(self.eco2_meter, 0, 2)
        meters_group.setLayout(meters_layout)
        layout.addWidget(meters_group)
        
        # Horizontal bars
        bars_group = QGroupBox("Gas Concentrations")
        bars_group.setStyleSheet("QGroupBox { color: #ffaa00; font-size: 13px; font-weight: bold; }")
        bars_layout = QVBoxLayout()
        bars_layout.setSpacing(10)
        
        self.tvoc_bar = HorizontalBar("TVOC", "ppb", 0, 10000, ['#00ff00', '#88ff00', '#ffff00', '#ff8800', '#ff0000'])
        self.co_bar = HorizontalBar("CO", "ppm", 0, 500, ['#00ff00', '#ffff00', '#ff8800', '#ff0000'])
        self.no2_bar = HorizontalBar("NO2", "ppb", 0, 500, ['#00ff00', '#ffff00', '#ff8800', '#ff0000'])
        
        bars_layout.addWidget(self.tvoc_bar)
        bars_layout.addWidget(self.co_bar)
        bars_layout.addWidget(self.no2_bar)
        bars_group.setLayout(bars_layout)
        layout.addWidget(bars_group)
        
        # Environmental data
        if self.has_env_data:
            env_group = QGroupBox("Environmental Conditions")
            env_group.setStyleSheet("QGroupBox { color: #00aaff; font-size: 13px; font-weight: bold; }")
            env_layout = QHBoxLayout()
            
            self.temp_meter = CircularMeter("Temperature", "°C", 0, 50, '#ff6b6b')
            self.humidity_meter = CircularMeter("Humidity", "%", 0, 100, '#4ecdc4')
            
            env_layout.addWidget(self.temp_meter)
            env_layout.addWidget(self.humidity_meter)
            env_group.setLayout(env_layout)
            layout.addWidget(env_group)
        
        # Last update
        self.time_label = QLabel("Last update: Never")
        self.time_label.setStyleSheet("font-size: 11px; color: #666666; margin-top: 10px;")
        layout.addWidget(self.time_label)
        
        layout.addStretch()
        self.setLayout(layout)
    
    def update_data(self, data):
        """Update all meters and bars"""
        self.status_label.setText("ONLINE")
        self.status_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #00ff00;")
        self.status_indicator.setStyleSheet("font-size: 40px; color: #00ff00;")
        
        if 'nodeType' in data:
            self.type_label.setText(f"Type: {data['nodeType']}")
        
        if 'pm25' in data:
            self.pm25_meter.setValue(data['pm25'])
        if 'pm10' in data:
            self.pm10_meter.setValue(data['pm10'])
        if 'eco2' in data:
            self.eco2_meter.setValue(data['eco2'])
        if 'tvoc' in data:
            self.tvoc_bar.setValue(data['tvoc'])
        if 'co' in data:
            self.co_bar.setValue(data['co'])
        if 'no2' in data:
            self.no2_bar.setValue(data['no2'])
        
        if self.has_env_data:
            if 'temp' in data:
                self.temp_meter.setValue(data['temp'])
            if 'humidity' in data:
                self.humidity_meter.setValue(data['humidity'])
        
        current_time = datetime.now().strftime("%H:%M:%S")
        self.time_label.setText(f"Last update: {current_time}")
    
    def set_offline(self):
        """Mark node as offline"""
        self.status_label.setText("OFFLINE")
        self.status_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #ff0000;")
        self.status_indicator.setStyleSheet("font-size: 40px; color: #ff0000;")


class GraphWidget(QWidget):
    """Widget with real-time graphs"""
    
    def __init__(self):
        super().__init__()
        self.max_points = 100
        self.init_data()
        self.init_ui()
        
    def init_data(self):
        """Initialize data storage"""
        self.time_data = deque(maxlen=self.max_points)
        self.data = {}
        for node in ['node1', 'node2', 'node3']:
            self.data[node] = {
                'pm25': deque(maxlen=self.max_points),
                'pm10': deque(maxlen=self.max_points),
                'eco2': deque(maxlen=self.max_points),
                'tvoc': deque(maxlen=self.max_points),
                'co': deque(maxlen=self.max_points),
                'no2': deque(maxlen=self.max_points),
            }
        self.start_time = datetime.now()
        
    def init_ui(self):
        main_layout = QVBoxLayout()
        
        self.plots = {}
        self.curves = {}
        
        parameters = [
            ('pm25', 'PM2.5 Levels', 'µg/m³'),
            ('pm10', 'PM10 Levels', 'µg/m³'),
            ('eco2', 'eCO2 Levels', 'ppm'),
            ('tvoc', 'TVOC Levels', 'ppb'),
            ('co', 'CO Levels', 'ppm'),
            ('no2', 'NO2 Levels', 'ppb'),
        ]
        
        colors = {
            'node1': '#00ff00',
            'node2': '#0088ff',
            'node3': '#ff8800'
        }
        
        for row in range(2):
            row_layout = QHBoxLayout()
            
            for col in range(3):
                idx = row * 3 + col
                if idx < len(parameters):
                    param, title, unit = parameters[idx]
                    
                    plot = PlotWidget(title=title)
                    plot.setLabel('left', param.upper(), units=unit)
                    plot.setLabel('bottom', 'Time', units='s')
                    plot.addLegend()
                    plot.showGrid(x=True, y=True, alpha=0.3)
                    
                    self.plots[param] = plot
                    self.curves[param] = {}
                    
                    for node in ['node1', 'node2', 'node3']:
                        node_num = node[-1]
                        self.curves[param][node] = plot.plot(
                            pen=pg.mkPen(color=colors[node], width=2),
                            name=f'Node {node_num}'
                        )
                    
                    row_layout.addWidget(plot)
            
            main_layout.addLayout(row_layout)
        
        self.setLayout(main_layout)
        
    def update_data(self, node_id, data):
        """Add new data point"""
        current_time = (datetime.now() - self.start_time).total_seconds()
        node_key = f'node{node_id}'
        
        # Only add time data once per update cycle
        if not self.time_data or current_time > self.time_data[-1]:
            self.time_data.append(current_time)
        
        for param in ['pm25', 'pm10', 'eco2', 'tvoc', 'co', 'no2']:
            if param in data and data[param] is not None:
                self.data[node_key][param].append(data[param])
        
    def update_plots(self):
        """Refresh all plots"""
        if len(self.time_data) == 0:
            return
            
        time_array = np.array(self.time_data)
        
        for node_key in ['node1', 'node2', 'node3']:
            for param in ['pm25', 'pm10', 'eco2', 'tvoc', 'co', 'no2']:
                data_list = list(self.data[node_key][param])
                if len(data_list) > 0:
                    # Ensure data and time arrays have same length
                    data_array = np.array(data_list[-len(time_array):])
                    x_data = time_array[-len(data_array):]
                    
                    if len(x_data) > 0 and len(data_array) > 0:
                        self.curves[param][node_key].setData(x_data, data_array)


class InfographicsWidget(QWidget):
    """Widget with unique visualizations and statistics - Advanced Analytics"""
    
    def __init__(self):
        super().__init__()
        self.node_data = {1: {}, 2: {}, 3: {}}
        self.history = {1: [], 2: [], 3: []}
        self.aqi_percentages = {1: 0, 2: 0, 3: 0}
        self.init_ui()
        
    def init_ui(self):
        main_layout = QHBoxLayout()
        
        left_layout = QVBoxLayout()
        
        aqi_group = QGroupBox("Air Quality Index Comparison")
        aqi_group.setStyleSheet("QGroupBox { color: #00ff88; font-size: 14px; font-weight: bold; }")
        aqi_layout = QHBoxLayout()
        
        self.aqi_bars = {}
        for i in range(1, 4):
            bar_widget = QWidget()
            bar_layout = QVBoxLayout(bar_widget)
            
            label = QLabel(f"Node {i}")
            label.setStyleSheet("font-size: 14px; font-weight: bold; color: #ffffff;")
            label.setAlignment(Qt.AlignCenter)
            bar_layout.addWidget(label)
            
            # Use custom vertical progress bar
            bar = VerticalGradientProgressBar(i)
            bar_layout.addWidget(bar)
            self.aqi_bars[i] = bar
            
            aqi_layout.addWidget(bar_widget)
        
        aqi_group.setLayout(aqi_layout)
        left_layout.addWidget(aqi_group)
        
        stats_group = QGroupBox("Network Statistics")
        stats_group.setStyleSheet("QGroupBox { color: #ffaa00; font-size: 14px; font-weight: bold; }")
        stats_layout = QGridLayout()
        
        stats_labels = [
            "Average PM2.5:", "Average CO:", "Peak eCO2:",
            "Min Temperature:", "Max Humidity:", "Active Nodes:"
        ]
        
        self.stat_values = {}
        for idx, label_text in enumerate(stats_labels):
            label = QLabel(label_text)
            label.setStyleSheet("font-size: 13px; color: #cccccc;")
            value = QLabel("--")
            value.setStyleSheet("font-size: 13px; font-weight: bold; color: #00aaff;")
            
            row = idx // 2
            col = (idx % 2) * 2
            stats_layout.addWidget(label, row, col)
            stats_layout.addWidget(value, row, col + 1)
            
            self.stat_values[label_text] = value
        
        stats_group.setLayout(stats_layout)
        left_layout.addWidget(stats_group)
        
        main_layout.addLayout(left_layout)
        
        right_layout = QVBoxLayout()
        
        # NEW: Pollutant Feature Importance Bar Chart
        self.feature_importance_plot = PlotWidget(title="Pollutant Feature Importance (Weighted Contribution)")
        self.feature_importance_plot.setLabel('left', 'Importance Score')
        self.feature_importance_plot.setLabel('bottom', 'Pollutant')
        self.feature_importance_plot.showGrid(x=True, y=True, alpha=0.3)
        self.feature_importance_plot.setYRange(0, 1)
        
        # Feature importance bars
        self.feature_bars = {}
        right_layout.addWidget(self.feature_importance_plot)
        
        # NEW: Pollutant Concentration Parallel Coordinates Plot
        self.parallel_plot = PlotWidget(title="Multi-Node Pollutant Comparison")
        self.parallel_plot.setLabel('left', 'Normalized Concentration')
        self.parallel_plot.setYRange(0, 1)
        self.parallel_plot.showGrid(x=False, y=True, alpha=0.3)
        
        # Remove axes for cleaner look
        self.parallel_plot.hideAxis('bottom')
        
        self.parallel_lines = {}
        right_layout.addWidget(self.parallel_plot)
        
        main_layout.addLayout(right_layout)
        
        self.setLayout(main_layout)
        
    def update_data(self, node_id, data):
        """Update infographics with new data"""
        self.node_data[node_id] = data
        
        # Store data for parallel coordinates plot
        if all(key in data for key in ['pm25', 'pm10', 'eco2', 'tvoc', 'co', 'no2']):
            self.history[node_id].append({
                'time': datetime.now(),
                'pm25': data['pm25'],
                'pm10': data['pm10'],
                'eco2': data['eco2'],
                'tvoc': data['tvoc'],
                'co': data['co'],
                'no2': data['no2']
            })
            
            # Keep only last 60 seconds
            cutoff_time = datetime.now().timestamp() - 60
            self.history[node_id] = [
                h for h in self.history[node_id]
                if h['time'].timestamp() > cutoff_time
            ]
            
            # Calculate AQI and percentage
            aqi = min(int(data['pm25'] * 1.5), 500)
            aqi_percentage = (aqi / 500) * 100
            self.aqi_percentages[node_id] = aqi_percentage
            
            if node_id in self.aqi_bars:
                self.aqi_bars[node_id].setValue(aqi, aqi_percentage)

    def update_statistics(self):
        """Update all statistics and visualizations"""
        all_pm25 = []
        all_co = []
        all_eco2 = []
        all_temp = []
        all_humidity = []
        active_nodes = 0
        
        # Calculate pollutant concentrations for all active nodes
        pollutant_data = []
        pollutants = ['pm25', 'pm10', 'eco2', 'tvoc', 'co', 'no2']
        
        for node_id, data in self.node_data.items():
            if data:
                active_nodes += 1
                if 'pm25' in data:
                    all_pm25.append(data['pm25'])
                if 'co' in data:
                    all_co.append(data['co'])
                if 'eco2' in data:
                    all_eco2.append(data['eco2'])
                if 'temp' in data:
                    all_temp.append(data['temp'])
                if 'humidity' in data:
                    all_humidity.append(data['humidity'])
                
                # Collect data for visualizations
                node_pollutants = []
                for pollutant in pollutants:
                    if pollutant in data:
                        node_pollutants.append(data[pollutant])
                    else:
                        node_pollutants.append(0)
                pollutant_data.append(node_pollutants)
        
        self.stat_values["Average PM2.5:"].setText(
            f"{np.mean(all_pm25):.1f} µg/m³" if all_pm25 else "--"
        )
        self.stat_values["Average CO:"].setText(
            f"{np.mean(all_co):.2f} ppm" if all_co else "--"
        )
        self.stat_values["Peak eCO2:"].setText(
            f"{max(all_eco2):.0f} ppm" if all_eco2 else "--"
        )
        self.stat_values["Min Temperature:"].setText(
            f"{min(all_temp):.1f} °C" if all_temp else "--"
        )
        self.stat_values["Max Humidity:"].setText(
            f"{max(all_humidity):.1f} %" if all_humidity else "--"
        )
        self.stat_values["Active Nodes:"].setText(f"{active_nodes}/3")
        
        # Update Feature Importance Bar Chart
        self.update_feature_importance(pollutant_data, pollutants)
        
        # Update Parallel Coordinates Plot
        self.update_parallel_coordinates(pollutant_data, pollutants)
    
    def update_feature_importance(self, pollutant_data, pollutants):
        """Update the feature importance bar chart"""
        self.feature_importance_plot.clear()
        
        if not pollutant_data:
            return
            
        # Calculate feature importance based on variance and correlation with AQI
        pollutant_array = np.array(pollutant_data)
        
        # Calculate normalized standard deviation (variability importance)
        std_values = np.std(pollutant_array, axis=0)
        std_normalized = std_values / np.max(std_values) if np.max(std_values) > 0 else np.zeros_like(std_values)
        
        # Calculate correlation with PM2.5 (as a proxy for AQI)
        pm25_index = pollutants.index('pm25')
        correlations = []
        for i in range(len(pollutants)):
            if i == pm25_index:
                correlations.append(1.0)
            else:
                corr = np.corrcoef(pollutant_array[:, pm25_index], pollutant_array[:, i])[0, 1]
                correlations.append(abs(corr) if not np.isnan(corr) else 0)
        
        # Combine both metrics (weighted average)
        importance_scores = 0.6 * std_normalized + 0.4 * np.array(correlations)
        
        # Normalize to 0-1 range
        if np.max(importance_scores) > 0:
            importance_scores = importance_scores / np.max(importance_scores)
        
        # Create bar chart
        x_positions = np.arange(len(pollutants))
        bar_width = 0.6
        
        # Create gradient colors based on importance
        colors = []
        for score in importance_scores:
            if score > 0.8:
                colors.append('#ff4444')  # High importance - red
            elif score > 0.6:
                colors.append('#ff8844')  # Medium-high - orange
            elif score > 0.4:
                colors.append('#ffcc44')  # Medium - yellow
            elif score > 0.2:
                colors.append('#88cc44')  # Medium-low - yellow-green
            else:
                colors.append('#44cc88')  # Low - green
        
        # Plot bars
        for i, (score, color, pollutant) in enumerate(zip(importance_scores, colors, pollutants)):
            # Create bar using Polygon
            bar = pg.PlotDataItem(
                [x_positions[i] - bar_width/2, x_positions[i] + bar_width/2, 
                 x_positions[i] + bar_width/2, x_positions[i] - bar_width/2],
                [0, 0, score, score],
                pen=pg.mkPen(color=color, width=2),
                fillLevel=0,
                fillBrush=pg.mkBrush(color=color + '80')
            )
            self.feature_importance_plot.addItem(bar)
            
            # Add value label on top of bar
            if score > 0.05:  # Only show label if bar is tall enough
                text = pg.TextItem(f"{score:.2f}", color='#ffffff', anchor=(0.5, 1))
                text.setPos(x_positions[i], score)
                self.feature_importance_plot.addItem(text)
            
            # Add pollutant name
            label = pg.TextItem(pollutant.upper(), color='#ffffff', anchor=(0.5, 1))
            label.setPos(x_positions[i], -0.05)
            self.feature_importance_plot.addItem(label)
        
        # Set X-axis range
        self.feature_importance_plot.setXRange(-0.5, len(pollutants) - 0.5)
        
        # Add horizontal grid lines
        for y in [0.2, 0.4, 0.6, 0.8, 1.0]:
            grid_line = pg.InfiniteLine(pos=y, angle=0, pen=pg.mkPen('#444444', width=1, style=Qt.DashLine))
            self.feature_importance_plot.addItem(grid_line)
    
    def update_parallel_coordinates(self, pollutant_data, pollutants):
        """Update the parallel coordinates plot"""
        self.parallel_plot.clear()
        
        if not pollutant_data:
            return
            
        pollutant_array = np.array(pollutant_data)
        
        # Normalize data to 0-1 range for each pollutant
        normalized_data = []
        for i in range(len(pollutants)):
            col_data = pollutant_array[:, i]
            if np.max(col_data) > np.min(col_data):
                normalized = (col_data - np.min(col_data)) / (np.max(col_data) - np.min(col_data))
            else:
                normalized = np.zeros_like(col_data)
            normalized_data.append(normalized)
        
        normalized_data = np.array(normalized_data).T
        
        # Define colors for different nodes
        node_colors = ['#00ff00', '#0088ff', '#ff8800']
        line_widths = [3, 2.5, 2]
        
        # Plot parallel coordinates for each node
        x_positions = np.arange(len(pollutants))
        
        for node_idx in range(min(3, len(normalized_data))):
            node_line = normalized_data[node_idx]
            
            # Create line through all pollutants
            line = pg.PlotDataItem(
                x_positions, node_line,
                pen=pg.mkPen(color=node_colors[node_idx], width=line_widths[node_idx]),
                symbol='o',
                symbolSize=10,
                symbolPen=pg.mkPen(color='#ffffff', width=1),
                symbolBrush=pg.mkBrush(color=node_colors[node_idx]),
                name=f'Node {node_idx + 1}'
            )
            self.parallel_plot.addItem(line)
            
            # Add node label at the end of the line
            label = pg.TextItem(f"Node {node_idx + 1}", color=node_colors[node_idx], anchor=(0, 0.5))
            label.setPos(len(pollutants) - 0.8, node_line[-1])
            self.parallel_plot.addItem(label)
        
        # Add vertical axes with pollutant labels
        for i, pollutant in enumerate(pollutants):
            # Add axis line
            axis_line = pg.InfiniteLine(pos=i, angle=90, pen=pg.mkPen('#666666', width=2))
            self.parallel_plot.addItem(axis_line)
            
            # Add pollutant label
            label = pg.TextItem(pollutant.upper(), color='#ffffff', anchor=(0.5, 1))
            label.setPos(i, -0.05)
            self.parallel_plot.addItem(label)
            
            # Add scale markers
            for y_val in [0.2, 0.4, 0.6, 0.8, 1.0]:
                marker = pg.PlotDataItem([i-0.05, i+0.05], [y_val, y_val], 
                                        pen=pg.mkPen('#666666', width=1))
                self.parallel_plot.addItem(marker)
                
                # Add scale label (only on first axis)
                if i == 0:
                    scale_label = pg.TextItem(f"{y_val:.1f}", color='#888888', anchor=(1, 0.5))
                    scale_label.setPos(-0.1, y_val)
                    self.parallel_plot.addItem(scale_label)
        
        # Set X-axis range
        self.parallel_plot.setXRange(-0.5, len(pollutants) - 0.5)
        
        # Add legend
        legend = self.parallel_plot.addLegend(offset=(10, 10))


class GeneralUserDashboard(QWidget):
    """Simplified Dashboard for general users with single pollutant bar chart"""
    
    def __init__(self):
        super().__init__()
        self.node_data = {1: {}, 2: {}, 3: {}}
        self.pollutant_data = {
            'PM2.5': 0,
            'PM10': 0,
            'eCO2': 0,
            'TVOC': 0,
            'CO': 0,
            'NO2': 0
        }
        self.init_ui()
        
    def init_ui(self):
        """Initialize UI"""
        main_layout = QVBoxLayout()
        
        # Title
        title_label = QLabel("🌿 AIR QUALITY MONITORING DASHBOARD")
        title_label.setStyleSheet("font-size: 28px; font-weight: bold; color: #00ffcc; margin: 10px 0;")
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)
        
        # Main content area
        content_layout = QHBoxLayout()
        
        # Left panel - Overview
        left_panel = QVBoxLayout()
        left_panel.setSpacing(15)
        
        # Health Indicator
        health_widget = self.create_health_indicator()
        left_panel.addWidget(health_widget)
        
        # Node Status
        status_widget = self.create_node_status()
        left_panel.addWidget(status_widget)
        
        # AQI Alert
        alert_widget = self.create_aqi_alert()
        left_panel.addWidget(alert_widget)
        
        # Environmental Conditions
        env_widget = self.create_environment_display()
        left_panel.addWidget(env_widget)
        
        content_layout.addLayout(left_panel, 35)  # 35% width
        
        # Right panel - Pollutant Bar Chart
        right_panel = QVBoxLayout()
        
        chart_group = QGroupBox("📊 Pollutant Levels Comparison")
        chart_group.setStyleSheet("""
            QGroupBox {
                color: #00ff88;
                font-size: 16px;
                font-weight: bold;
                border: 2px solid #444444;
                border-radius: 10px;
                padding-top: 15px;
                margin: 10px;
            }
        """)
        
        chart_layout = QVBoxLayout()
        
        # Create the bar chart
        self.pollutant_plot = PlotWidget(title="Current Pollutant Concentrations")
        self.pollutant_plot.setLabel('left', 'Concentration')
        self.pollutant_plot.setLabel('bottom', 'Pollutants')
        self.pollutant_plot.showGrid(x=True, y=True, alpha=0.3)
        self.pollutant_plot.setBackground('#1e1e1e')
        
        # Initialize bars
        self.pollutant_bars = {}
        self.pollutant_labels = {}
        
        chart_layout.addWidget(self.pollutant_plot)
        
        # Legend
        legend_widget = self.create_chart_legend()
        chart_layout.addWidget(legend_widget)
        
        chart_group.setLayout(chart_layout)
        right_panel.addWidget(chart_group)
        
        content_layout.addLayout(right_panel, 65)  # 65% width
        
        main_layout.addLayout(content_layout)
        
        # Footer
        footer = QLabel("Real-time air quality monitoring system | Data updates every second")
        footer.setStyleSheet("font-size: 10px; color: #888888; margin-top: 10px;")
        footer.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(footer)
        
        self.setLayout(main_layout)
        
        # Initialize chart
        self.initialize_pollutant_chart()
    
    def create_health_indicator(self):
        """Create overall health indicator"""
        widget = QGroupBox("🏥 Overall Air Quality")
        widget.setStyleSheet("""
            QGroupBox {
                color: #00aaff;
                font-size: 14px;
                font-weight: bold;
                border: 2px solid #444444;
                border-radius: 8px;
                padding-top: 12px;
            }
        """)
        
        layout = QVBoxLayout()
        
        self.health_value = QLabel("--")
        self.health_value.setStyleSheet("font-size: 48px; font-weight: bold; color: #ffffff;")
        self.health_value.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.health_value)
        
        self.health_status = QLabel("No Data")
        self.health_status.setStyleSheet("font-size: 16px; font-weight: bold; color: #888888;")
        self.health_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.health_status)
        
        widget.setLayout(layout)
        return widget
    
    def create_node_status(self):
        """Create node status indicators"""
        widget = QGroupBox("📡 Sensor Status")
        widget.setStyleSheet("""
            QGroupBox {
                color: #ffaa00;
                font-size: 14px;
                font-weight: bold;
                border: 2px solid #444444;
                border-radius: 8px;
                padding-top: 12px;
            }
        """)
        
        layout = QGridLayout()
        
        self.node_status_labels = {}
        node_colors = ['#00ff00', '#0088ff', '#ff8800']
        
        for i in range(3):
            # Status indicator
            indicator = QLabel("●")
            indicator.setStyleSheet(f"font-size: 24px; color: #ff0000;")
            layout.addWidget(indicator, 0, i*2)
            
            # Node info
            node_info = QVBoxLayout()
            
            node_name = QLabel(f"Node {i+1}")
            node_name.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {node_colors[i]};")
            node_info.addWidget(node_name)
            
            status_label = QLabel("OFFLINE")
            status_label.setStyleSheet("font-size: 11px; color: #ff0000;")
            node_info.addWidget(status_label)
            
            layout.addLayout(node_info, 0, i*2+1)
            
            self.node_status_labels[i+1] = {
                'indicator': indicator,
                'status': status_label
            }
        
        widget.setLayout(layout)
        return widget
    
    def create_aqi_alert(self):
        """Create AQI alert display"""
        widget = QGroupBox("🚨 AQI Alert Level")
        widget.setStyleSheet("""
            QGroupBox {
                color: #ff4444;
                font-size: 14px;
                font-weight: bold;
                border: 2px solid #444444;
                border-radius: 8px;
                padding-top: 12px;
            }
        """)
        
        layout = QVBoxLayout()
        
        self.aqi_level = QLabel("NO DATA")
        self.aqi_level.setStyleSheet("""
            font-size: 24px;
            font-weight: bold;
            color: #888888;
            padding: 10px;
            background-color: #2d2d2d;
            border-radius: 6px;
            border: 2px solid #888888;
        """)
        self.aqi_level.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.aqi_level)
        
        self.aqi_description = QLabel("Connect sensors to monitor air quality")
        self.aqi_description.setStyleSheet("font-size: 11px; color: #888888;")
        self.aqi_description.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.aqi_description)
        
        widget.setLayout(layout)
        return widget
    
    def create_environment_display(self):
        """Create temperature and humidity display"""
        widget = QGroupBox("🌡️ Environment")
        widget.setStyleSheet("""
            QGroupBox {
                color: #ff66cc;
                font-size: 14px;
                font-weight: bold;
                border: 2px solid #444444;
                border-radius: 8px;
                padding-top: 12px;
            }
        """)
        
        layout = QHBoxLayout()
        
        # Temperature
        temp_layout = QVBoxLayout()
        temp_icon = QLabel("🌡️")
        temp_icon.setStyleSheet("font-size: 24px;")
        temp_icon.setAlignment(Qt.AlignCenter)
        temp_layout.addWidget(temp_icon)
        
        self.temp_value = QLabel("--°C")
        self.temp_value.setStyleSheet("font-size: 20px; font-weight: bold; color: #ffffff;")
        self.temp_value.setAlignment(Qt.AlignCenter)
        temp_layout.addWidget(self.temp_value)
        
        temp_label = QLabel("Temperature")
        temp_label.setStyleSheet("font-size: 11px; color: #ff6b6b;")
        temp_label.setAlignment(Qt.AlignCenter)
        temp_layout.addWidget(temp_label)
        
        # Humidity
        hum_layout = QVBoxLayout()
        hum_icon = QLabel("💧")
        hum_icon.setStyleSheet("font-size: 24px;")
        hum_icon.setAlignment(Qt.AlignCenter)
        hum_layout.addWidget(hum_icon)
        
        self.hum_value = QLabel("--%")
        self.hum_value.setStyleSheet("font-size: 20px; font-weight: bold; color: #ffffff;")
        self.hum_value.setAlignment(Qt.AlignCenter)
        hum_layout.addWidget(self.hum_value)
        
        hum_label = QLabel("Humidity")
        hum_label.setStyleSheet("font-size: 11px; color: #4ecdc4;")
        hum_label.setAlignment(Qt.AlignCenter)
        hum_layout.addWidget(hum_label)
        
        layout.addLayout(temp_layout)
        layout.addLayout(hum_layout)
        
        widget.setLayout(layout)
        return widget
    
    def create_chart_legend(self):
        """Create legend for the chart"""
        widget = QFrame()
        widget.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border: 1px solid #444444;
                border-radius: 6px;
                padding: 8px;
                margin: 5px;
            }
        """)
        
        layout = QHBoxLayout()
        
        levels = [
            ("Good", "#00ff00"),
            ("Moderate", "#ffff00"),
            ("Unhealthy", "#ff8800"),
            ("Very Unhealthy", "#ff0000"),
            ("Hazardous", "#9900cc")
        ]
        
        for name, color in levels:
            item = QFrame()
            item.setFixedSize(15, 15)
            item.setStyleSheet(f"background-color: {color}; border-radius: 3px;")
            layout.addWidget(item)
            
            label = QLabel(name)
            label.setStyleSheet("font-size: 9px; color: #cccccc; margin-right: 10px;")
            layout.addWidget(label)
        
        layout.addStretch()
        widget.setLayout(layout)
        return widget
    
    def initialize_pollutant_chart(self):
        """Initialize the pollutant bar chart"""
        self.pollutant_plot.clear()
        
        pollutants = ['PM2.5', 'PM10', 'eCO2', 'TVOC', 'CO', 'NO2']
        x_positions = np.arange(len(pollutants))
        
        # Set up the chart
        self.pollutant_plot.setXRange(-0.5, len(pollutants) - 0.5)
        self.pollutant_plot.setYRange(0, 100)  # Initial range
        
        # Add pollutant labels
        for i, pollutant in enumerate(pollutants):
            label = pg.TextItem(pollutant, color='#ffffff', anchor=(0.5, 1))
            label.setPos(i, -5)
            self.pollutant_plot.addItem(label)
    
    def update_pollutant_chart(self):
        """Update the pollutant bar chart with current data"""
        self.pollutant_plot.clear()
        
        pollutants = ['PM2.5', 'PM10', 'eCO2', 'TVOC', 'CO', 'NO2']
        x_positions = np.arange(len(pollutants))
        
        # Get current values
        values = [
            self.pollutant_data['PM2.5'],
            self.pollutant_data['PM10'],
            self.pollutant_data['eCO2'],
            self.pollutant_data['TVOC'],
            self.pollutant_data['CO'],
            self.pollutant_data['NO2']
        ]
        
        # Determine colors based on values
        colors = []
        for pollutant, value in zip(pollutants, values):
            if pollutant == 'PM2.5':
                if value <= 12:
                    colors.append('#00ff00')
                elif value <= 35:
                    colors.append('#ffff00')
                elif value <= 55:
                    colors.append('#ff8800')
                elif value <= 150:
                    colors.append('#ff0000')
                else:
                    colors.append('#9900cc')
            elif pollutant == 'PM10':
                if value <= 20:
                    colors.append('#00ff00')
                elif value <= 50:
                    colors.append('#ffff00')
                elif value <= 100:
                    colors.append('#ff8800')
                elif value <= 200:
                    colors.append('#ff0000')
                else:
                    colors.append('#9900cc')
            elif pollutant == 'eCO2':
                if value <= 600:
                    colors.append('#00ff00')
                elif value <= 1000:
                    colors.append('#ffff00')
                elif value <= 1500:
                    colors.append('#ff8800')
                elif value <= 2000:
                    colors.append('#ff0000')
                else:
                    colors.append('#9900cc')
            elif pollutant == 'TVOC':
                if value <= 200:
                    colors.append('#00ff00')
                elif value <= 500:
                    colors.append('#ffff00')
                elif value <= 1000:
                    colors.append('#ff8800')
                elif value <= 2000:
                    colors.append('#ff0000')
                else:
                    colors.append('#9900cc')
            elif pollutant == 'CO':
                if value <= 2:
                    colors.append('#00ff00')
                elif value <= 5:
                    colors.append('#ffff00')
                elif value <= 10:
                    colors.append('#ff8800')
                elif value <= 20:
                    colors.append('#ff0000')
                else:
                    colors.append('#9900cc')
            elif pollutant == 'NO2':
                if value <= 20:
                    colors.append('#00ff00')
                elif value <= 50:
                    colors.append('#ffff00')
                elif value <= 100:
                    colors.append('#ff8800')
                elif value <= 200:
                    colors.append('#ff0000')
                else:
                    colors.append('#9900cc')
        
        # Create bars
        bars = pg.BarGraphItem(x=x_positions, height=values, width=0.6, brushes=colors)
        self.pollutant_plot.addItem(bars)
        
        # Add value labels on top of bars
        for i, (value, color) in enumerate(zip(values, colors)):
            if value > 0:
                text = pg.TextItem(f"{value:.1f}", color='#ffffff', anchor=(0.5, 1))
                text.setPos(i, value)
                self.pollutant_plot.addItem(text)
        
        # Add pollutant labels
        for i, pollutant in enumerate(pollutants):
            label = pg.TextItem(pollutant, color='#ffffff', anchor=(0.5, 1))
            label.setPos(i, -5)
            self.pollutant_plot.addItem(label)
        
        # Auto-adjust Y range
        max_value = max(values) if values else 100
        self.pollutant_plot.setYRange(0, max_value * 1.2)
    
    def update_data(self, node_id, data):
        """Update dashboard with new data"""
        self.node_data[node_id] = data
        
        # Update node status
        if node_id in self.node_status_labels:
            self.node_status_labels[node_id]['indicator'].setStyleSheet(
                f"font-size: 24px; color: #00ff00;"
            )
            self.node_status_labels[node_id]['status'].setText("ONLINE")
            self.node_status_labels[node_id]['status'].setStyleSheet(
                "font-size: 11px; color: #00ff00;"
            )
        
        # Update pollutant data (use the latest value from any node)
        if 'pm25' in data:
            self.pollutant_data['PM2.5'] = data['pm25']
        if 'pm10' in data:
            self.pollutant_data['PM10'] = data['pm10']
        if 'eco2' in data:
            self.pollutant_data['eCO2'] = data['eco2']
        if 'tvoc' in data:
            self.pollutant_data['TVOC'] = data['tvoc']
        if 'co' in data:
            self.pollutant_data['CO'] = data['co']
        if 'no2' in data:
            self.pollutant_data['NO2'] = data['no2']
        
        # Update health indicator
        self.update_health_indicator()
        
        # Update AQI alert
        self.update_aqi_alert()
        
        # Update environmental data
        if 'temp' in data:
            self.temp_value.setText(f"{data['temp']:.1f}°C")
        if 'humidity' in data:
            self.hum_value.setText(f"{data['humidity']:.1f}%")
        
        # Update chart
        self.update_pollutant_chart()
    
    def update_health_indicator(self):
        """Update health indicator based on PM2.5"""
        pm25 = self.pollutant_data['PM2.5']
        
        if pm25 == 0:
            self.health_value.setText("--")
            self.health_status.setText("No Data")
            self.health_status.setStyleSheet("font-size: 16px; font-weight: bold; color: #888888;")
            return
        
        if pm25 <= 12:
            score = 95
            status = "EXCELLENT"
            color = "#00ff00"
        elif pm25 <= 35:
            score = 75
            status = "GOOD"
            color = "#88ff00"
        elif pm25 <= 55:
            score = 50
            status = "FAIR"
            color = "#ffff00"
        elif pm25 <= 150:
            score = 30
            status = "POOR"
            color = "#ff8800"
        else:
            score = 10
            status = "HAZARDOUS"
            color = "#ff0000"
        
        self.health_value.setText(f"{score}")
        self.health_value.setStyleSheet(f"font-size: 48px; font-weight: bold; color: {color};")
        self.health_status.setText(status)
        self.health_status.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {color};")
    
    def update_aqi_alert(self):
        """Update AQI alert based on PM2.5"""
        pm25 = self.pollutant_data['PM2.5']
        
        if pm25 == 0:
            self.aqi_level.setText("NO DATA")
            self.aqi_level.setStyleSheet("""
                font-size: 24px;
                font-weight: bold;
                color: #888888;
                padding: 10px;
                background-color: #2d2d2d;
                border-radius: 6px;
                border: 2px solid #888888;
            """)
            self.aqi_description.setText("Connect sensors to monitor air quality")
            self.aqi_description.setStyleSheet("font-size: 11px; color: #888888;")
            return
        
        if pm25 <= 12:
            level = "GOOD"
            color = "#00ff00"
            desc = "Air quality is satisfactory"
        elif pm25 <= 35:
            level = "MODERATE"
            color = "#ffff00"
            desc = "Air quality is acceptable"
        elif pm25 <= 55:
            level = "UNHEALTHY"
            color = "#ff8800"
            desc = "Sensitive groups should limit outdoor activity"
        elif pm25 <= 150:
            level = "VERY UNHEALTHY"
            color = "#ff0000"
            desc = "Everyone may begin to experience health effects"
        else:
            level = "HAZARDOUS"
            color = "#9900cc"
            desc = "Emergency conditions: avoid all outdoor activity"
        
        self.aqi_level.setText(level)
        self.aqi_level.setStyleSheet(f"""
            font-size: 24px;
            font-weight: bold;
            color: {color};
            padding: 10px;
            background-color: #2d2d2d;
            border-radius: 6px;
            border: 2px solid {color};
        """)
        self.aqi_description.setText(desc)
        self.aqi_description.setStyleSheet(f"font-size: 11px; color: {color};")


class MainWindow(QMainWindow):
    """Main application window"""
    
    def __init__(self):
        super().__init__()
        self.serial_reader = None
        self.node_data = {}
        self.db_manager = DatabaseManager()
        self.last_db_save = {1: None, 2: None, 3: None}
        self.current_tab = 0
        self.init_ui()
        
    def init_ui(self):
        """Initialize UI"""
        self.setWindowTitle("Air Quality Monitoring Dashboard with Database")
        self.setGeometry(100, 100, 1600, 900)
        
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #1e1e1e;
                color: #ffffff;
            }
            QGroupBox {
                border: 2px solid #444444;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 15px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
            QPushButton {
                background-color: #007acc;
                color: white;
                border: none;
                padding: 10px 15px;
                border-radius: 5px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #005a9e;
            }
            QPushButton:pressed {
                background-color: #004578;
            }
            QComboBox {
                background-color: #2d2d2d;
                border: 1px solid #555555;
                padding: 8px;
                border-radius: 4px;
                font-size: 11px;
            }
            QTabWidget::pane {
                border: 1px solid #444444;
            }
            QTabBar::tab {
                background-color: #2d2d2d;
                color: #ffffff;
                padding: 12px 25px;
                border: 1px solid #444444;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background-color: #007acc;
            }
        """)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # Connection controls
        conn_layout = QHBoxLayout()
        
        conn_label = QLabel("Serial Port:")
        conn_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        conn_layout.addWidget(conn_label)
        
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(200)
        self.refresh_ports()
        conn_layout.addWidget(self.port_combo)
        
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh_ports)
        conn_layout.addWidget(refresh_btn)
        
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.toggle_connection)
        conn_layout.addWidget(self.connect_btn)
        
        self.status_label = QLabel("Disconnected")
        self.status_label.setStyleSheet("font-weight: bold; color: #ff0000; font-size: 12px;")
        conn_layout.addWidget(self.status_label)
        
        conn_layout.addStretch()
        
        # Database status indicator
        self.db_status_label = QLabel("📊 Database: Active")
        self.db_status_label.setStyleSheet("font-weight: bold; color: #00ff00; font-size: 12px;")
        conn_layout.addWidget(self.db_status_label)
        
        main_layout.addLayout(conn_layout)
        
        # Tab widget
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self.on_tab_changed)
        
        # General User Dashboard (NEW SIMPLIFIED VERSION)
        scroll_general = QScrollArea()
        scroll_general.setWidgetResizable(True)
        self.general_dashboard = GeneralUserDashboard()
        scroll_general.setWidget(self.general_dashboard)
        self.tabs.addTab(scroll_general, "🏠 General Dashboard")
        
        # Node tabs
        scroll1 = QScrollArea()
        scroll1.setWidgetResizable(True)
        self.node1_widget = NodeWidget(1, has_env_data=True)
        scroll1.setWidget(self.node1_widget)
        self.tabs.addTab(scroll1, "🔵 Node 1")
        
        scroll2 = QScrollArea()
        scroll2.setWidgetResizable(True)
        self.node2_widget = NodeWidget(2, has_env_data=True)
        scroll2.setWidget(self.node2_widget)
        self.tabs.addTab(scroll2, "🟢 Node 2")
        
        scroll3 = QScrollArea()
        scroll3.setWidgetResizable(True)
        self.node3_widget = NodeWidget(3, has_env_data=False)
        scroll3.setWidget(self.node3_widget)
        self.tabs.addTab(scroll3, "🟠 Node 3")
        
        # Graphs tab
        scroll_graphs = QScrollArea()
        scroll_graphs.setWidgetResizable(True)
        self.graph_widget = GraphWidget()
        scroll_graphs.setWidget(self.graph_widget)
        self.tabs.addTab(scroll_graphs, "📊 Real-time Graphs")
        
        # Infographics tab (renamed to Advanced Analytics)
        scroll_info = QScrollArea()
        scroll_info.setWidgetResizable(True)
        self.infographics_widget = InfographicsWidget()
        scroll_info.setWidget(self.infographics_widget)
        self.tabs.addTab(scroll_info, "📈 Advanced Analytics")
        
        # Database view tab
        scroll_db = QScrollArea()
        scroll_db.setWidgetResizable(True)
        self.database_widget = DatabaseViewWidget(self.db_manager)
        scroll_db.setWidget(self.database_widget)
        self.tabs.addTab(scroll_db, "💾 Database Monitor")
        
        main_layout.addWidget(self.tabs)
        
        # Timers
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_all)
        self.update_timer.start(1000)
        
        self.db_timer = QTimer()
        self.db_timer.timeout.connect(self.check_db_save)
        self.db_timer.start(1000)
        
        self.node_widgets = {
            1: self.node1_widget,
            2: self.node2_widget,
            3: self.node3_widget
        }

    def on_tab_changed(self, index):
        """Handle tab change to optimize performance"""
        self.current_tab = index
        
    def refresh_ports(self):
        """Refresh available serial ports"""
        import serial.tools.list_ports
        self.port_combo.clear()
        ports = serial.tools.list_ports.comports()
        for port in ports:
            self.port_combo.addItem(f"{port.device} - {port.description}")
            
    def toggle_connection(self):
        """Connect or disconnect from serial port"""
        if self.serial_reader and self.serial_reader.running:
            self.serial_reader.stop()
            self.connect_btn.setText("Connect")
            self.status_label.setText("Disconnected")
            self.status_label.setStyleSheet("font-weight: bold; color: #ff0000; font-size: 12px;")
        else:
            port_text = self.port_combo.currentText()
            if port_text:
                port = port_text.split(' - ')[0]
                self.serial_reader = SerialReader(port)
                self.serial_reader.data_received.connect(self.handle_data)
                self.serial_reader.status_update.connect(self.update_status)
                self.serial_reader.start()
                self.connect_btn.setText("Disconnect")
                
    def handle_data(self, data):
        """Handle received data"""
        if 'nodeId' in data:
            node_id = data['nodeId']
            if 1 <= node_id <= 3:
                if node_id in self.node_widgets:
                    self.node_widgets[node_id].update_data(data)
                
                self.graph_widget.update_data(node_id, data)
                self.infographics_widget.update_data(node_id, data)
                self.general_dashboard.update_data(node_id, data)
                
                # Store for database saving
                self.node_data[node_id] = {
                    'data': data,
                    'timestamp': datetime.now()
                }
                
    def check_db_save(self):
        """Check if it's time to save data to database (every 5 seconds)"""
        current_time = datetime.now()
        
        for node_id, node_info in self.node_data.items():
            last_save = self.last_db_save[node_id]
            
            if last_save is None or (current_time - last_save).total_seconds() >= 5:
                # Save to database
                success = self.db_manager.insert_sensor_data(node_id, node_info['data'])
                if success:
                    self.last_db_save[node_id] = current_time
                
    def update_status(self, status):
        """Update connection status"""
        self.status_label.setText(status)
        if "Connected" in status:
            self.status_label.setStyleSheet("font-weight: bold; color: #00ff00; font-size: 12px;")
        else:
            self.status_label.setStyleSheet("font-weight: bold; color: #ff0000; font-size: 12px;")
            
    def update_all(self):
        """Update all visualizations periodically"""
        self.graph_widget.update_plots()
        self.infographics_widget.update_statistics()
        
        # Only update database tab if it's visible (performance optimization)
        if self.current_tab == 6:  # Database Monitor tab
            self.database_widget.update_statistics()
            self.database_widget.update_table()
        
        # Check for offline nodes
        current_time = datetime.now()
        for node_id, node_info in list(self.node_data.items()):
            if 'timestamp' in node_info:
                time_diff = (current_time - node_info['timestamp']).total_seconds()
                if time_diff > 60:
                    if node_id in self.node_widgets:
                        self.node_widgets[node_id].set_offline()
                    # Also update general dashboard
                    if node_id in self.general_dashboard.node_status_labels:
                        self.general_dashboard.node_status_labels[node_id]['indicator'].setStyleSheet(
                            "font-size: 24px; color: #ff0000;"
                        )
                        self.general_dashboard.node_status_labels[node_id]['status'].setText("OFFLINE")
                        self.general_dashboard.node_status_labels[node_id]['status'].setStyleSheet(
                            "font-size: 11px; color: #ff0000;"
                        )
                    
    def closeEvent(self, event):
        """Clean up on close"""
        if self.serial_reader:
            self.serial_reader.stop()
        self.db_manager.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()