# Hunting Together RPG Help Tool

เครื่องมือช่วยเล่น Hunting Together RPG (Roblox)

| โฟลเดอร์ | ทำอะไร |
| --- | --- |
| [`BotLevel99/`](BotLevel99/README.md) | บอทบน LDPlayer: เก็บเลเวลตามแผน, Auto Gear/Card, ย่อยของ, อัพ Stats ตามตาราง, ล่าการ์ดบอสโลกพร้อมย้ายห้อง และหน้าควบคุมบนเบราว์เซอร์ คุมเกมผ่าน adb ใน LDPlayer อย่างเดียว ไม่ขยับเมาส์หรือคีย์บอร์ดของ Windows |
| [`RoomSpoofer/`](RoomSpoofer/README.md) | ส่วนเสริม Chrome สำหรับหน้าเกมบน roblox.com: ดึงรายชื่อห้อง เรียงตามปิงหรือคนน้อย แล้วกดเข้าห้องที่เลือก (มี Auto Join และคูลดาวน์ห้องที่เคยเข้า) และมาโคร LDPlayer กดปุ่ม E ใน `RoomSpoofer/ldplayer/` |

## สิ่งที่ต้องมี (สรุป)

| เครื่องมือ | ต้องมี |
| --- | --- |
| BotLevel99 | Windows 10/11 ที่มีภาษา English (United States), Python 3.11+ (ติ๊ก Add to PATH), LDPlayer 9 ตั้ง 1280x720 แนวนอน DPI 240 และเปิด ADB debugging, แอป Roblox ใน LDPlayer ที่ล็อกอินแล้วและเกมเป็นภาษาอังกฤษ ส่วน library Python `run.bat` ลงให้เองใน `.venv` |
| RoomSpoofer | Google Chrome (หรือ Edge), ล็อกอิน roblox.com, Roblox Player บน PC |

รายละเอียดเต็ม วิธีติดตั้ง และวิธีใช้งานอยู่ใน README ของแต่ละโฟลเดอร์
