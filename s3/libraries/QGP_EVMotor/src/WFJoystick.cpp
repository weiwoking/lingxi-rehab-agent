#include "WFJoystick.h"
using namespace std::placeholders;
void WFJoystick::begin()
{
    mqtt_client.setServer(mqtt_broker.c_str(), mqtt_port);  
    mqtt_client.setCallback(std::bind(&WFJoystick::mqtt_callback, this, _1, _2, _3));
}

WFJoystick::WFJoystick(String mqtt_broker, int mqtt_port, String mqtt_username, String mqtt_password,String uid) : mqtt_client(wifiClient)
{
  this->mqtt_broker = mqtt_broker;
  this->mqtt_port = mqtt_port;
  this->mqtt_username = mqtt_username;
  this->mqtt_password = mqtt_password;
  this->uid = uid;
  this->client_id = "Mixly_" + WiFi.macAddress();
  this->mqtt_topic = String("evtopic/wfjy/"+uid); 
  this->mqtt_topic_pub = String("evtopic/wfjy/"+uid+"/pub");
}

/****************************************************************************************/
boolean WFJoystick::NewButtonState()
{
    return ((last_buttons ^ buttons) > 0);
}

/****************************************************************************************/
boolean WFJoystick::NewButtonState(uint32_t button)
{
    return (((last_buttons ^ buttons) & button) > 0);
}

/****************************************************************************************/
boolean WFJoystick::ButtonPressed(uint32_t button)
{
    return (NewButtonState(button) & Button(button));
}

/****************************************************************************************/
boolean WFJoystick::ButtonReleased(uint32_t button)
{
    return ((NewButtonState(button)) & ((last_buttons & button) > 0));
}

/****************************************************************************************/
boolean WFJoystick::Button(uint32_t button)
{
    // LOG("BTN:%d %d \n", buttons, button);
    return ((buttons & button) > 0);
}

/****************************************************************************************/
uint32_t WFJoystick::ButtonDataByte()
{
    return (buttons);
}

/****************************************************************************************/
byte WFJoystick::Analog(byte button)
{
    return AXISData[button];
}

void WFJoystick::reConnect()
{
    if (!mqtt_client.connected())
    {
        while (!mqtt_client.connected())
        {
            if (mqtt_client.connect(("Mixly_" + WiFi.macAddress()).c_str(), mqtt_username.c_str(), mqtt_password.c_str()))
            {
                Serial.printf("cid:%s 已登录,订阅话题: %s\n", client_id, mqtt_topic.c_str());
                mqtt_client.subscribe(mqtt_topic.c_str());
            }
            else
            {
                Serial.println("Mqtt登录失败");
                delay(1000);
            }
        }
    }
}

void WFJoystick::readCommand()
{
  reConnect();
  mqtt_client.loop();
}

boolean WFJoystick::isEnabled()
{
  return true;
}


void WFJoystick::mqtt_callback(char *topic, byte *payload, unsigned int length){
  memset(&receive_Head, 0, sizeof(receive_Head));
  memcpy(&receive_Head, payload, 2); 
  if (receive_Head.commandID == 0x01)
  {
    // prev_cmd_time = millis();
    uint32_t btn = 0;
    memset(&receive_joyData, 0, sizeof(receive_joyData));
    memcpy(&receive_joyData, payload, 10);
    btn = (uint32_t)(int(receive_joyData.buttons[2]) << 16 | int(receive_joyData.buttons[1] << 8) | receive_joyData.buttons[0]);
    AXISData[0]=receive_joyData.lx;
    AXISData[1]=receive_joyData.ly;
    AXISData[2]=receive_joyData.rx;
    AXISData[3]=receive_joyData.ry;
    last_buttons=buttons;
    buttons = btn;
    // _cmdHandler.onStickChanged(receive_joyData.lx, (int)receive_joyData.ly, (int)receive_joyData.rx, (int)receive_joyData.ry, 0, 0, btn);
  }else if (receive_Head.commandID == 0x02)
  {
    Serial.print("live-->");
    mqtt_client.publish(mqtt_topic_pub.c_str(),(const char*)payload);
    Serial.print("finish!");
  }
}