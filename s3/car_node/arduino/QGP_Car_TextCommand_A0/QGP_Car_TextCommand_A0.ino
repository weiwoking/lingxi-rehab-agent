/**
奇果派Esp32-S3电机驱动板示例代码：蓝牙手柄控制麦克纳姆轮智能小车
蓝牙手柄控制4电机 4舵机
左侧摇杆：车子前进后退 + 左右横移
右侧摇杆：车子前进后退 + 左右转弯
左侧方向键：上下舵机1  左右舵机2
右侧按钮：YA舵机3 XB舵机4

本版仅重构输入层：
- 开机默认 SERIAL，供 P4 正式控制；
- MODE BLE：进入蓝牙手柄测试模式；
- MODE UDP：进入旧 UDP 测试模式；
- MODE SERIAL：返回正式串口模式；
- 三种输入源不会同时驱动车；
- Serial 使用非阻塞逐行解析；
- STOP 始终优先，并回到 SERIAL 模式；
- SERIAL / UDP 300ms 无新 PAD 自动停车；
- 下方电机方向、速度映射与舵机动作保持原基线结构。
*/

#include <Arduino.h>
#include <EMotionPI.h>
#include <BLEControlStick.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include "wifi_secrets.h"

BLEControlStick _joy;
EMotionPI emo;

/**4个普通电机*/
EMO_DCMotor *enMotor1 = emo.getMotor(M1);
EMO_DCMotor *enMotor2 = emo.getMotor(M2);
EMO_DCMotor *enMotor3 = emo.getMotor(M3);
EMO_DCMotor *enMotor4 = emo.getMotor(M4);

/**4个舵机*/
EMO_Servo *servo1 = emo.getServo(S1);
EMO_Servo *servo2 = emo.getServo(S2);
EMO_Servo *servo3 = emo.getServo(S3);
EMO_Servo *servo4 = emo.getServo(S4);

float cur_speed = 100;  // 运动速度 0-100


/*
 * 三种输入模式严格互斥。
 * 正式运行默认 SERIAL；BLE 与 UDP 仅在明确切换后启用。
 */
enum class ControlMode : uint8_t
{
  SERIAL_MODE = 0,
  BLE_MODE,
  UDP_MODE,
};

struct ControlFrame
{
  int lx;
  int ly;
  int rx;
  int ry;

  bool btnA;
  bool btnB;
  bool btnX;
  bool btnY;
  bool btnL2;
  bool btnR2;
  bool btnUp;
  bool btnDown;
  bool btnLeft;
  bool btnRight;

  bool valid;
  unsigned long receivedMs;
};

ControlMode controlMode = ControlMode::SERIAL_MODE;
ControlFrame controlFrame;

bool bleConnected = false;
bool motorsStopped = true;

// 左右轮组机械补偿：M1/M3 为左侧，M2/M4 为右侧。
// 110 表示左侧提高 10%，100 表示右侧保持原输出。
const int LEFT_MOTOR_SCALE_PERCENT  = 120;
const int RIGHT_MOTOR_SCALE_PERCENT = 105;

int applyMotorScale(int speed, int scalePercent)
{
  long scaled = (long)speed * scalePercent;

  if (scaled >= 0)
  {
    scaled = (scaled + 50) / 100;
  }
  else
  {
    scaled = (scaled - 50) / 100;
  }

  return constrain((int)scaled, -100, 100);
}

/*
 * Serial 非阻塞行缓冲。
 * 支持 LF 与 CRLF，不再使用 readStringUntil() 阻塞 loop。
 */
const size_t SERIAL_LINE_BUFFER_SIZE = 128;
const uint8_t SERIAL_MAX_LINES_PER_LOOP = 8;
char serialLineBuffer[SERIAL_LINE_BUFFER_SIZE];
size_t serialLineLength = 0;
bool serialLineOverflow = false;

/*
 * UDP 仅在 MODE UDP 时启用。
 */
WiFiUDP udp;
const uint16_t UDP_CONTROL_PORT = 5010;
const unsigned long WIFI_RETRY_INTERVAL_MS = 3000;
const unsigned long REMOTE_CONTROL_TIMEOUT_MS = 300;

bool wifiControlStarted = false;
bool udpStarted = false;
unsigned long lastWifiRetryMs = 0;

/*
***************************************************************************************************
* StickCB类 - 处理蓝牙手柄事件
***************************************************************************************************
*/
class StickCB : public StickCallback
{
public:
  StickCB() {}

  void onConnect()
  {
    bleConnected = true;
    Serial.println("[BLE] CONNECTED");
  }

  void onDisconnect()
  {
    bleConnected = false;
    Serial.println("[BLE] DISCONNECTED");
  }

  void onStickChanged(const ButtonEvent &event)
  {
    event.button(BTN_A) ? Serial.println("A键按下") : Serial.println("A键未按下");
    Serial.printf("onStickChanged: %d %d %d %d\n",
                  event.getAxisLX(),
                  event.getAxisLY(),
                  event.getAxisRX(),
                  event.getAxisRY());
  }

  void onButton(const ButtonEvent &event)
  {
    if (event.button(BTN_A)) Serial.println("按住A键...");
  }

  void onButtonChanged(const ButtonEvent &event)
  {
    Serial.printf("======onButtonChanged: %d===========\n", event.getButtonState());
  }

  void onButtonPressed(const ButtonEvent &event) override
  {
    if (event & BTN_A) Serial.println("按下了A键");
    if (event & BTN_B) Serial.println("按下了B键");
    if (event & BTN_X) Serial.println("按下了X键");
    if (event & BTN_Y) Serial.println("按下了Y键");
    if (event & BTN_L1) Serial.println("按下了L1键");
    if (event & BTN_R1) Serial.println("按下了R1键");

    if (event.button(BTN_START)) Serial.println("按下了START键");
    if (event.button(BTN_SEL)) Serial.println("按下了SELECT键");

    if (event.button(BTN_DPAD_UP)) Serial.println("按下了UP键");
    if (event.button(BTN_DPAD_DOWN)) Serial.println("按下了DOWN键");
    if (event.button(BTN_DPAD_LEFT)) Serial.println("按下了LEFT键");
    if (event.button(BTN_DPAD_RIGHT)) Serial.println("按下了RIGHT键");
  }

  void onButtonReleased(const ButtonEvent &event) override
  {
    if (event & BTN_A) Serial.println("释放了A键");
  }
};

/**刹车
brakeForce：刹车力度，不是持续时间*/
void stop(int brakeForce)
{
  enMotor1->brake(brakeForce);
  enMotor2->brake(brakeForce);
  enMotor3->brake(brakeForce);
  enMotor4->brake(brakeForce);
  motorsStopped = true;
}

void setNeutralFrame(ControlFrame &frame)
{
  frame.lx = 128;
  frame.ly = 128;
  frame.rx = 128;
  frame.ry = 128;

  frame.btnA = false;
  frame.btnB = false;
  frame.btnX = false;
  frame.btnY = false;
  frame.btnL2 = false;
  frame.btnR2 = false;
  frame.btnUp = false;
  frame.btnDown = false;
  frame.btnLeft = false;
  frame.btnRight = false;

  frame.valid = false;
  frame.receivedMs = 0;
}

void clearControlFrame()
{
  setNeutralFrame(controlFrame);
}

const char *controlModeName(ControlMode mode)
{
  switch (mode)
  {
    case ControlMode::SERIAL_MODE: return "SERIAL";
    case ControlMode::BLE_MODE: return "BLE";
    case ControlMode::UDP_MODE: return "UDP";
    default: return "UNKNOWN";
  }
}

void printControlStatus()
{
  Serial.printf("[CTRL] STATUS mode=%s frame_valid=%d age_ms=%lu ble_connected=%d "
                "wifi_started=%d udp_started=%d motors_stopped=%d\n",
                controlModeName(controlMode),
                controlFrame.valid ? 1 : 0,
                controlFrame.valid ? (millis() - controlFrame.receivedMs) : 0,
                bleConnected ? 1 : 0,
                wifiControlStarted ? 1 : 0,
                udpStarted ? 1 : 0,
                motorsStopped ? 1 : 0);
}

void stopAndClear(const char *reason)
{
  clearControlFrame();
  stop(100);
  if (reason != nullptr)
  {
    Serial.printf("[CTRL] STOP reason=%s ms=%lu\n", reason, millis());
  }
}

/*
 * UDP 生命周期仅属于 UDP 模式。
 */
void stopWifiControl()
{
  if (udpStarted)
  {
    udp.stop();
    udpStarted = false;
    Serial.println("[UDP] STOPPED");
  }

  if (wifiControlStarted)
  {
    WiFi.disconnect(true, false);
    WiFi.mode(WIFI_OFF);
    wifiControlStarted = false;
    Serial.println("[WIFI] OFF");
  }
}

void beginWifiControl()
{
  if (wifiControlStarted)
  {
    return;
  }

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  lastWifiRetryMs = millis();
  wifiControlStarted = true;

  Serial.println("[WIFI] STA_START");
  Serial.println("[WIFI] CONNECTING...");
}

void setControlMode(ControlMode newMode, const char *reason)
{
  if (controlMode == newMode)
  {
    stopAndClear("MODE_REASSERT");
    Serial.printf("[CTRL] MODE active=%s reason=%s\n",
                  controlModeName(controlMode),
                  reason != nullptr ? reason : "NONE");
    return;
  }

  stopAndClear("MODE_CHANGE");

  if (controlMode == ControlMode::UDP_MODE)
  {
    stopWifiControl();
  }

  controlMode = newMode;

  if (controlMode == ControlMode::UDP_MODE)
  {
    beginWifiControl();
  }

  Serial.printf("[CTRL] MODE active=%s reason=%s\n",
                controlModeName(controlMode),
                reason != nullptr ? reason : "NONE");
}

/*
 * 解析工具。
 */
bool parseAxis(String field, int &value)
{
  field.trim();
  if (field.length() == 0)
  {
    return false;
  }

  for (unsigned int i = 0; i < field.length(); i++)
  {
    if (!isDigit(field[i]))
    {
      return false;
    }
  }

  value = field.toInt();
  return value >= 0 && value <= 255;
}

bool addButtonToFrame(String button, ControlFrame &frame)
{
  button.trim();
  button.toUpperCase();

  if (button == "" || button == "NONE") return true;
  if (button == "A") { frame.btnA = true; return true; }
  if (button == "B") { frame.btnB = true; return true; }
  if (button == "X") { frame.btnX = true; return true; }
  if (button == "Y") { frame.btnY = true; return true; }
  if (button == "L2") { frame.btnL2 = true; return true; }
  if (button == "R2") { frame.btnR2 = true; return true; }
  if (button == "UP" || button == "DPAD_UP") { frame.btnUp = true; return true; }
  if (button == "DOWN" || button == "DPAD_DOWN") { frame.btnDown = true; return true; }
  if (button == "LEFT" || button == "DPAD_LEFT") { frame.btnLeft = true; return true; }
  if (button == "RIGHT" || button == "DPAD_RIGHT") { frame.btnRight = true; return true; }

  return false;
}

bool parseButtonField(String field, ControlFrame &frame)
{
  field.trim();
  field.toUpperCase();

  if (field == "" || field == "NONE")
  {
    return true;
  }

  int start = 0;
  while (start < field.length())
  {
    int split = field.indexOf('|', start);
    String oneButton;

    if (split < 0)
    {
      oneButton = field.substring(start);
      start = field.length();
    }
    else
    {
      oneButton = field.substring(start, split);
      start = split + 1;
    }

    if (!addButtonToFrame(oneButton, frame))
    {
      return false;
    }
  }

  return true;
}

bool parsePadFrame(String command, ControlFrame &parsedFrame)
{
  command.trim();
  command.toUpperCase();

  String field[6];
  int begin = 0;

  for (int i = 0; i < 5; i++)
  {
    int comma = command.indexOf(',', begin);
    if (comma < 0)
    {
      return false;
    }

    field[i] = command.substring(begin, comma);
    begin = comma + 1;
  }

  if (command.indexOf(',', begin) >= 0)
  {
    return false;
  }

  field[5] = command.substring(begin);
  field[0].trim();

  setNeutralFrame(parsedFrame);

  int lx;
  int ly;
  int rx;
  int ry;

  if (field[0] != "PAD" ||
      !parseAxis(field[1], lx) ||
      !parseAxis(field[2], ly) ||
      !parseAxis(field[3], rx) ||
      !parseAxis(field[4], ry) ||
      !parseButtonField(field[5], parsedFrame))
  {
    return false;
  }

  parsedFrame.lx = lx;
  parsedFrame.ly = ly;
  parsedFrame.rx = rx;
  parsedFrame.ry = ry;
  parsedFrame.valid = true;
  parsedFrame.receivedMs = millis();
  return true;
}

void acceptPadFrame(const ControlFrame &frame, const char *source, const String &buttonField)
{
  controlFrame = frame;

  Serial.printf("[PAD] INPUT source=%s,%d,%d,%d,%d,%s\n",
                source,
                controlFrame.lx,
                controlFrame.ly,
                controlFrame.rx,
                controlFrame.ry,
                buttonField.c_str());
}

/*
 * Serial 管理与控制命令。
 * MODE / STATUS / HELP 在所有模式可用。
 * STOP 在所有模式优先，并把系统带回 SERIAL。
 * PAD 只有 SERIAL 模式接受。
 */
void processSerialCommand(String command)
{
  command.trim();
  command.toUpperCase();

  if (command.length() == 0)
  {
    return;
  }

  Serial.printf("[SERIAL] RECV,%s\n", command.c_str());

  if (command == "STATUS")
  {
    printControlStatus();
    return;
  }

  if (command == "HELP")
  {
    Serial.println("[CTRL] COMMANDS: MODE SERIAL | MODE BLE | MODE UDP | STATUS | STOP");
    Serial.println("[CTRL] PAD: PAD,LX,LY,RX,RY,BUTTONS");
    return;
  }

  if (command == "MODE SERIAL")
  {
    setControlMode(ControlMode::SERIAL_MODE, "SERIAL_COMMAND");
    return;
  }

  if (command == "MODE BLE")
  {
    setControlMode(ControlMode::BLE_MODE, "SERIAL_COMMAND");
    return;
  }

  if (command == "MODE UDP")
  {
    setControlMode(ControlMode::UDP_MODE, "SERIAL_COMMAND");
    return;
  }

  if (command == "STOP")
  {
    if (controlMode != ControlMode::SERIAL_MODE)
    {
      setControlMode(ControlMode::SERIAL_MODE, "SERIAL_STOP");
    }
    else
    {
      stopAndClear("SERIAL_STOP");
    }

    Serial.println("[PAD] STOP source=SERIAL");
    return;
  }

  if (command.startsWith("PAD"))
  {
    if (controlMode != ControlMode::SERIAL_MODE)
    {
      Serial.printf("[PAD] IGNORED source=SERIAL active_mode=%s\n",
                    controlModeName(controlMode));
      return;
    }

    ControlFrame parsedFrame;

    if (!parsePadFrame(command, parsedFrame))
    {
      stopAndClear("SERIAL_PAD_INVALID");
      Serial.println("[PAD] ERROR source=SERIAL");
      return;
    }

    int lastComma = command.lastIndexOf(',');
    String buttonField = lastComma >= 0 ? command.substring(lastComma + 1) : "NONE";
    acceptPadFrame(parsedFrame, "SERIAL", buttonField);
    return;
  }

  Serial.println("[SERIAL] ERROR,UNKNOWN_COMMAND");
}

/*
 * 非阻塞 Serial 读取。
 * 每轮最多处理固定数量完整行，防止异常输入长期占用 loop。
 */
void serviceSerialInput()
{
  uint8_t processedLines = 0;

  while (Serial.available() > 0 && processedLines < SERIAL_MAX_LINES_PER_LOOP)
  {
    char ch = static_cast<char>(Serial.read());

    if (ch == '\r')
    {
      continue;
    }

    if (ch == '\n')
    {
      if (serialLineOverflow)
      {
        stopAndClear("SERIAL_LINE_OVERFLOW");
        Serial.println("[SERIAL] ERROR,LINE_TOO_LONG");
      }
      else if (serialLineLength > 0)
      {
        serialLineBuffer[serialLineLength] = '\0';
        processSerialCommand(String(serialLineBuffer));
      }

      serialLineLength = 0;
      serialLineOverflow = false;
      processedLines++;
      continue;
    }

    if (serialLineOverflow)
    {
      continue;
    }

    if (serialLineLength < SERIAL_LINE_BUFFER_SIZE - 1)
    {
      serialLineBuffer[serialLineLength++] = ch;
    }
    else
    {
      serialLineOverflow = true;
    }
  }
}

/*
 * 维护 UDP 模式的 Wi-Fi。
 */
void maintainWifiControl()
{
  if (!wifiControlStarted || controlMode != ControlMode::UDP_MODE)
  {
    return;
  }

  if (WiFi.status() == WL_CONNECTED)
  {
    if (!udpStarted)
    {
      if (udp.begin(UDP_CONTROL_PORT))
      {
        udpStarted = true;
        Serial.printf("[WIFI] CONNECTED,IP=%s\n", WiFi.localIP().toString().c_str());
        Serial.printf("[UDP] LISTEN,PORT=%u\n", UDP_CONTROL_PORT);
      }
      else
      {
        Serial.println("[UDP] ERROR,BEGIN_FAILED");
      }
    }
    return;
  }

  if (udpStarted)
  {
    udp.stop();
    udpStarted = false;
    Serial.println("[UDP] STOPPED,WIFI_DISCONNECTED");
  }

  if (millis() - lastWifiRetryMs >= WIFI_RETRY_INTERVAL_MS)
  {
    lastWifiRetryMs = millis();
    Serial.println("[WIFI] RETRY");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }
}

void processUdpCommand(String command)
{
  command.trim();
  command.toUpperCase();

  if (command.length() == 0)
  {
    return;
  }

  Serial.printf("[UDP] RECV,%s\n", command.c_str());

  if (command == "STOP")
  {
    stopAndClear("UDP_STOP");
    Serial.println("[PAD] STOP source=UDP");
    return;
  }

  ControlFrame parsedFrame;

  if (!parsePadFrame(command, parsedFrame))
  {
    stopAndClear("UDP_PAD_INVALID");
    Serial.println("[PAD] ERROR source=UDP");
    return;
  }

  int lastComma = command.lastIndexOf(',');
  String buttonField = lastComma >= 0 ? command.substring(lastComma + 1) : "NONE";
  acceptPadFrame(parsedFrame, "UDP", buttonField);
}

void checkUdpInput()
{
  if (controlMode != ControlMode::UDP_MODE || !udpStarted)
  {
    return;
  }

  const int UDP_COMMAND_BUFFER_SIZE = 96;
  int packetSize = udp.parsePacket();

  while (packetSize > 0)
  {
    if (packetSize >= UDP_COMMAND_BUFFER_SIZE)
    {
      while (udp.available() > 0)
      {
        udp.read();
      }

      stopAndClear("UDP_PACKET_TOO_LONG");
      Serial.println("[UDP] ERROR,PACKET_TOO_LONG");
      packetSize = udp.parsePacket();
      continue;
    }

    char commandBuffer[UDP_COMMAND_BUFFER_SIZE];
    int len = udp.read(commandBuffer, UDP_COMMAND_BUFFER_SIZE - 1);

    if (len > 0)
    {
      commandBuffer[len] = '\0';
      processUdpCommand(String(commandBuffer));
    }

    packetSize = udp.parsePacket();
  }
}

/*
 * BLE 只在 MODE BLE 中参与控制。
 * 未连接时强制停车，不读取库里可能残留的旧轴值。
 */
void updateBleControlFrame()
{
  if (controlMode != ControlMode::BLE_MODE)
  {
    return;
  }

  if (!bleConnected)
  {
    if (controlFrame.valid || !motorsStopped)
    {
      stopAndClear("BLE_NOT_CONNECTED");
    }
    return;
  }

  controlFrame.lx = _joy.Analog(BPSS_LX);
  controlFrame.ly = _joy.Analog(BPSS_LY);
  controlFrame.rx = _joy.Analog(BPSS_RX);
  controlFrame.ry = _joy.Analog(BPSS_RY);

  controlFrame.btnA = _joy.Button(BTN_A);
  controlFrame.btnB = _joy.Button(BTN_B);
  controlFrame.btnX = _joy.Button(BTN_X);
  controlFrame.btnY = _joy.Button(BTN_Y);
  controlFrame.btnL2 = _joy.Button(BTN_L2);
  controlFrame.btnR2 = _joy.Button(BTN_R2);
  controlFrame.btnUp = _joy.Button(BTN_DPAD_UP);
  controlFrame.btnDown = _joy.Button(BTN_DPAD_DOWN);
  controlFrame.btnLeft = _joy.Button(BTN_DPAD_LEFT);
  controlFrame.btnRight = _joy.Button(BTN_DPAD_RIGHT);

  controlFrame.valid = true;
  controlFrame.receivedMs = millis();
}

/*
 * SERIAL / UDP 仅依据“最后一条合法 PAD 的到达时间”超时。
 * 不再依据循环中重复使用的旧速度刷新时间。
 */
void checkRemoteControlTimeout()
{
  if ((controlMode == ControlMode::SERIAL_MODE ||
       controlMode == ControlMode::UDP_MODE) &&
      controlFrame.valid &&
      millis() - controlFrame.receivedMs >= REMOTE_CONTROL_TIMEOUT_MS)
  {
    stopAndClear("REMOTE_TIMEOUT");
    Serial.println("[PAD] AUTO_STOP,TIMEOUT");
  }
}

/*
 * 运动层只读取统一 ControlFrame，不关心来源。
 */
int inputLX()
{
  return controlFrame.valid ? controlFrame.lx : 128;
}

int inputLY()
{
  return controlFrame.valid ? controlFrame.ly : 128;
}

int inputRX()
{
  return controlFrame.valid ? controlFrame.rx : 128;
}

int inputRY()
{
  return controlFrame.valid ? controlFrame.ry : 128;
}

template <typename T>
bool inputButton(T button)
{
  if (!controlFrame.valid)
  {
    return false;
  }

  if (button == BTN_A) return controlFrame.btnA;
  if (button == BTN_B) return controlFrame.btnB;
  if (button == BTN_X) return controlFrame.btnX;
  if (button == BTN_Y) return controlFrame.btnY;
  if (button == BTN_L2) return controlFrame.btnL2;
  if (button == BTN_R2) return controlFrame.btnR2;
  if (button == BTN_DPAD_UP) return controlFrame.btnUp;
  if (button == BTN_DPAD_DOWN) return controlFrame.btnDown;
  if (button == BTN_DPAD_LEFT) return controlFrame.btnLeft;
  if (button == BTN_DPAD_RIGHT) return controlFrame.btnRight;

  return false;
}

void setup()
{
  Serial.begin(115200);
  Serial.setTimeout(20);

  clearControlFrame();

  _joy.setStickCallback(new StickCB());
  _joy.begin();

  delay(100);

  servo1->write(90);
  servo2->write(90);
  servo3->write(90);
  servo4->write(90);

  stop(100);

  Serial.println("启动完毕");
  Serial.println("[CTRL] Default mode: SERIAL");
  Serial.println("[CTRL] MODE SERIAL | MODE BLE | MODE UDP");
  Serial.println("[CTRL] STATUS | HELP | STOP");
  Serial.println("[PAD] Format: PAD,LX,LY,RX,RY,BUTTONS");
  Serial.println("[PAD] Example: PAD,128,80,128,128,NONE");
}

void loop()
{
  /*
   * Serial 管理/STOP 永远先处理。
   */
  serviceSerialInput();

  if (controlMode == ControlMode::BLE_MODE)
  {
    /*
     * BLE 连接过程可能阻塞，但只存在于明确的测试模式。
     * 未连接前车保持停止。
     */
    if (!bleConnected && !motorsStopped)
    {
      stopAndClear("BLE_CONNECTING");
    }

    _joy.update();

    /*
     * 若 BLE update 期间收到了串口 MODE/STOP，返回后立刻处理，
     * 再决定是否仍允许 BLE 数据进入控制帧。
     */
    serviceSerialInput();

    if (controlMode == ControlMode::BLE_MODE)
    {
      updateBleControlFrame();
    }
  }
  else if (controlMode == ControlMode::UDP_MODE)
  {
    maintainWifiControl();
    serviceSerialInput();

    if (controlMode == ControlMode::UDP_MODE)
    {
      checkUdpInput();
    }
  }

  /*
   * SERIAL 模式不运行 BLE 与 Wi-Fi 控制逻辑。
   * 再处理一次串口，缩短本轮中途到达 STOP 的等待。
   */
  serviceSerialInput();
  checkRemoteControlTimeout();

  const int lx = inputLX();
  const int ly = inputLY();
  const int rx = inputRX();
  const int ry = inputRY();

  //舵机1
  if (inputButton(BTN_DPAD_UP))
  {
    if (servo1->read() < 175)
      servo1->write(servo1->read() + 1);
  }
  else if (inputButton(BTN_DPAD_DOWN))
  {
    if (servo1->read() > 5)
      servo1->write(servo1->read() - 1);
  }

  //舵机2
  if (inputButton(BTN_DPAD_LEFT))
  {
    if (servo2->read() < 175)
      servo2->write(servo2->read() + 1);
  }
  else if (inputButton(BTN_DPAD_RIGHT))
  {
    if (servo2->read() > 5)
      servo2->write(servo2->read() - 1);
  }

  //舵机3
  if (inputButton(BTN_Y))
  {
    if (servo3->read() < 175)
      servo3->write(servo3->read() + 1);
  }
  else if (inputButton(BTN_A))
  {
    if (servo3->read() > 5)
      servo3->write(servo3->read() - 1);
  }

  //舵机4
  if (inputButton(BTN_X))
  {
    if (servo4->read() < 175)
      servo4->write(servo4->read() + 1);
  }
  else if (inputButton(BTN_B))
  {
    if (servo4->read() > 5)
      servo4->write(servo4->read() - 1);
  }

  if (inputButton(BTN_R2) || inputButton(BTN_L2))
  {
    stopAndClear("PAD_EMERGENCY_BUTTON");
    return;
  }

  int speed = 0;
  bool motorCommandActive = false;

  // 左侧摇杆控制车子前进后退 + 左右横移
  if (ly < 125)
  {
    speed = map(ly, 125, 0, 30, cur_speed);
    enMotor1->run(FORWARD);  enMotor1->setSpeed(applyMotorScale(speed, LEFT_MOTOR_SCALE_PERCENT));
    enMotor2->run(FORWARD);  enMotor2->setSpeed(applyMotorScale(speed, RIGHT_MOTOR_SCALE_PERCENT));
    enMotor3->run(BACKWARD); enMotor3->setSpeed(applyMotorScale(speed, LEFT_MOTOR_SCALE_PERCENT));
    enMotor4->run(BACKWARD); enMotor4->setSpeed(applyMotorScale(speed, RIGHT_MOTOR_SCALE_PERCENT));
    motorCommandActive = true;
  }
  else if (ly > 130)
  {
    speed = map(ly, 130, 255, 30, cur_speed);
    enMotor1->run(BACKWARD); enMotor1->setSpeed(applyMotorScale(speed, LEFT_MOTOR_SCALE_PERCENT));
    enMotor2->run(BACKWARD); enMotor2->setSpeed(applyMotorScale(speed, RIGHT_MOTOR_SCALE_PERCENT));
    enMotor3->run(FORWARD);  enMotor3->setSpeed(applyMotorScale(speed, LEFT_MOTOR_SCALE_PERCENT));
    enMotor4->run(FORWARD);  enMotor4->setSpeed(applyMotorScale(speed, RIGHT_MOTOR_SCALE_PERCENT));
    motorCommandActive = true;
  }

  if (lx < 125)
  {
    speed = map(lx, 125, 0, 30, cur_speed);
    enMotor1->run(BACKWARD); enMotor1->setSpeed(applyMotorScale(speed, LEFT_MOTOR_SCALE_PERCENT));
    enMotor2->run(FORWARD);  enMotor2->setSpeed(applyMotorScale(speed, RIGHT_MOTOR_SCALE_PERCENT));
    enMotor3->run(BACKWARD); enMotor3->setSpeed(applyMotorScale(speed, LEFT_MOTOR_SCALE_PERCENT));
    enMotor4->run(FORWARD);  enMotor4->setSpeed(applyMotorScale(speed, RIGHT_MOTOR_SCALE_PERCENT));
    motorCommandActive = true;
  }
  else if (lx > 130)
  {
    speed = map(lx, 130, 255, 30, cur_speed);
    enMotor1->run(FORWARD);  enMotor1->setSpeed(applyMotorScale(speed, LEFT_MOTOR_SCALE_PERCENT));
    enMotor2->run(BACKWARD); enMotor2->setSpeed(applyMotorScale(speed, RIGHT_MOTOR_SCALE_PERCENT));
    enMotor3->run(FORWARD);  enMotor3->setSpeed(applyMotorScale(speed, LEFT_MOTOR_SCALE_PERCENT));
    enMotor4->run(BACKWARD); enMotor4->setSpeed(applyMotorScale(speed, RIGHT_MOTOR_SCALE_PERCENT));
    motorCommandActive = true;
  }

  // 右侧摇杆控制车子前进后退 + 左右旋转
  if (ry < 125)
  {
    speed = map(ry, 125, 0, 30, cur_speed);
    enMotor1->run(FORWARD);  enMotor1->setSpeed(applyMotorScale(speed, LEFT_MOTOR_SCALE_PERCENT));
    enMotor2->run(FORWARD);  enMotor2->setSpeed(applyMotorScale(speed, RIGHT_MOTOR_SCALE_PERCENT));
    enMotor3->run(BACKWARD); enMotor3->setSpeed(applyMotorScale(speed, LEFT_MOTOR_SCALE_PERCENT));
    enMotor4->run(BACKWARD); enMotor4->setSpeed(applyMotorScale(speed, RIGHT_MOTOR_SCALE_PERCENT));
    Serial.printf("up:%d,%d\n", ry, speed);
    motorCommandActive = true;
  }
  else if (ry > 130)
  {
    speed = map(ry, 130, 255, 30, cur_speed);
    enMotor1->run(BACKWARD); enMotor1->setSpeed(applyMotorScale(speed, LEFT_MOTOR_SCALE_PERCENT));
    enMotor2->run(BACKWARD); enMotor2->setSpeed(applyMotorScale(speed, RIGHT_MOTOR_SCALE_PERCENT));
    enMotor3->run(FORWARD);  enMotor3->setSpeed(applyMotorScale(speed, LEFT_MOTOR_SCALE_PERCENT));
    enMotor4->run(FORWARD);  enMotor4->setSpeed(applyMotorScale(speed, RIGHT_MOTOR_SCALE_PERCENT));
    Serial.printf("down:%d,%d\n", ry, speed);
    motorCommandActive = true;
  }

  if (rx < 125)
  {
    speed = map(rx, 125, 0, 30, cur_speed);
    enMotor1->run(BACKWARD); enMotor1->setSpeed(applyMotorScale(speed, LEFT_MOTOR_SCALE_PERCENT));
    enMotor2->run(FORWARD);  enMotor2->setSpeed(applyMotorScale(speed, RIGHT_MOTOR_SCALE_PERCENT));
    enMotor3->run(FORWARD);  enMotor3->setSpeed(applyMotorScale(speed, LEFT_MOTOR_SCALE_PERCENT));
    enMotor4->run(BACKWARD); enMotor4->setSpeed(applyMotorScale(speed, RIGHT_MOTOR_SCALE_PERCENT));
    Serial.printf("left:%d,%d\n", rx, speed);
    motorCommandActive = true;
  }
  else if (rx > 130)
  {
    /*
     * 保留用户给定基线中的右旋速度与四电机组合，不在本轮改运动映射。
     */
    speed = -map(rx, 130, 255, 30, cur_speed);
    enMotor1->run(FORWARD);  enMotor1->setSpeed(applyMotorScale(speed, LEFT_MOTOR_SCALE_PERCENT));
    enMotor2->run(BACKWARD); enMotor2->setSpeed(applyMotorScale(speed, RIGHT_MOTOR_SCALE_PERCENT));
    enMotor3->run(BACKWARD); enMotor3->setSpeed(applyMotorScale(speed, LEFT_MOTOR_SCALE_PERCENT));
    enMotor4->run(FORWARD);  enMotor4->setSpeed(applyMotorScale(speed, RIGHT_MOTOR_SCALE_PERCENT));
    Serial.printf("right:%d,%d\n", rx, speed);
    motorCommandActive = true;
  }

  /*
   * 没有任何运动轴时立即保持刹车，避免旧电机输出被锁存。
   */
  if (motorCommandActive)
  {
    motorsStopped = false;
  }
  else if (!motorsStopped)
  {
    stop(100);
  }
}
