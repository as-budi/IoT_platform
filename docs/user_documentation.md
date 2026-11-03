# IoT Platform — User documentation

This file contains every chapter of the platform documentation at https://iot.reslab-csub.cloud/docs/. 
Addresses and topics use the values of that server; `<token>` is a device's access token and `<prefix>` the server's MQTT topic prefix, both shown on each device page.

## Contents

- **Basics**
  - [Getting started](#getting-started)
  - [Your account](#account)
- **Devices and data**
  - [Devices and locations](#devices)
  - [Variables](#variables)
  - [Dashboard, charts and history](#dashboard)
  - [Controlling devices](#control)
- **Connecting devices**
  - [Connect an ESP32](#connect-esp32)
  - [Device API reference](#device-api)
  - [Security and certificates](#security)
  - [Firmware updates and MAC address](#firmware-updates)
- **Automation**
  - [Rules overview](#rules)
  - [ECA rules](#rules-eca)
  - [Rule chains](#rule-chains)
  - [State machines](#state-machines)
  - [Schedules](#schedules)
  - [Alarms and notifications](#alarms-notifications)
- **Working together**
  - [Sharing devices](#sharing)
  - [Activity log](#activity-log)
- **Apps and integrations**
  - [MQTT sandbox](#mqtt-sandbox)
  - [Connected apps and API](#connected-apps)
- **Help**
  - [Troubleshooting](#troubleshooting)
  - [For administrators](#administration)

## Basics

<a id="getting-started"></a>

### Getting started

*What the platform does, the main ideas, and your first device in a few steps.*

IoT Platform collects data from your devices (for example an ESP32 with sensors), shows it on a dashboard, lets you switch actuators such as pumps and lamps, and reacts automatically with rules, alarms and notifications.

<a id="getting-started--concepts"></a>

#### Main ideas

|           |                                                                                                                                                                               |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Device    | One physical board, e.g. "Greenhouse node". Every device has a secret **access token** that it uses to send data.                                                             |
| Variable  | One value of a device, identified by a **key** such as `temperature`. A *sensor* variable is only read; an *actuator* variable can also be switched or set from the platform. |
| Telemetry | The readings a device sends, e.g. `{"temperature": 25.1, "humidity": 60}`. Each reading is stored with its time.                                                              |
| Location  | A place on the map that groups devices, e.g. "Lab 3" or "Greenhouse A".                                                                                                       |
| Rule      | An automation, e.g. "if soil moisture is below 20 % for one minute, switch the pump on and raise an alarm".                                                                   |
| Alarm     | A problem raised by a rule. Someone acknowledges it; it clears when the cause is gone.                                                                                        |

<a id="getting-started--first-device"></a>

#### Your first device in five steps

1.  **Create an account** and activate it with the link in the email ([Your account](#account)).

2.  **Add a device**: Devices → *Add device*. Give it a name; type, location and description are optional.

3.  **Send data.** Open the device page and copy its access token. The quickest test is a single command from a computer:
    
        curl -X POST https://iot.reslab-csub.cloud/api/v1/<token>/telemetry -H "Content-Type: application/json" -d '{"temperature": 25.1}'
    
    Replace `<token>` with the access token. For a real board, follow [Connect an ESP32](#connect-esp32).

4.  **Watch the data.** The variable `temperature` is created automatically and appears on the device page and the dashboard ([Dashboard, charts and history](#dashboard)).

5.  **Automate.** Create a rule that reacts to the data ([Rules overview](#rules)).

<a id="getting-started--menu"></a>

#### Where to find things

| Menu           | What it is for                                                                                                                |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Dashboard      | Every variable of every device, grouped by location, updated live.                                                            |
| Devices        | Your devices and devices shared with you; each device page has its data, controls, commands, firmware and connection details. |
| Locations      | Places on a map that group devices.                                                                                           |
| Sharing groups | Lists of users you share devices with, e.g. a class team.                                                                     |
| Activity log   | What happened to your devices, rules and account.                                                                             |
| Rules, Alarms  | Automation and the problems it reports.                                                                                       |
| Firmware       | Firmware files for updating devices over the air.                                                                             |
| Documentation  | These pages. Use the search box to find any topic.                                                                            |
| My profile     | Your name, email, password, Telegram, MQTT sandbox and connected apps (click your name at the bottom of the menu).            |

Most pages also have a **Help** button that opens the matching chapter.

On a large screen, the button next to *IoT Platform* at the top of the menu hides the menu to give the page more room; the button in the top-left corner shows it again. Your choice is remembered in this browser. On a small screen the menu opens from the ☰ button.

<a id="getting-started--live"></a>

#### Live updates

Pages with data update by themselves. The switch *Live updates* at the top of those pages shows the state of the connection and can pause updates. While you type in a form, the page waits until you are done, so nothing you typed is lost.

<a id="account"></a>

### Your account

*Signing up, logging in, passwords, email address and profile.*

<a id="account--sign-up"></a>

#### Signing up

1.  Click *Sign up* on the login page and fill in the form.
2.  We send an activation link to your email address. It works once and expires after 24 hours.
3.  Open the link and click *Activate account*. You can now log in.

No email? Check the spam folder. On the login page, *Resend activation link* sends a new one. New accounts get the **User** role; only an administrator can change roles.

<a id="account--password"></a>

#### Forgotten or changing your password

  - **Forgotten:** on the login page click *Forgot your password?*, enter your email address and open the link we send. The link expires after 24 hours.
  - **Changing:** My profile → *Change password*. You are logged out of every other browser.

<a id="account--profile"></a>

#### My profile

Open it by clicking your name at the bottom of the side menu. There you can:

  - change your first name, last name and email address (a new email address is used only after you confirm it from that inbox);
  - connect Telegram to receive notifications from rules ([Telegram](#alarms-notifications--telegram));
  - create an MQTT sandbox account ([MQTT sandbox](#mqtt-sandbox));
  - see and remove apps that sign you in with your account ([Connected apps and API](#connected-apps));
  - change your password.

Your username and role can only be changed by an administrator.

<a id="account--roles"></a>

#### Roles

|       |                                                                                                    |
| ----- | -------------------------------------------------------------------------------------------------- |
| User  | Sees and manages their own devices, locations, rules and firmware, plus devices shared with them.  |
| Admin | Sees everything on the platform and manages user accounts ([For administrators](#administration)). |

Access to one device can also be given to other users with the roles Viewer, Operator and Editor; see [Sharing devices](#sharing).

## Devices and data

<a id="devices"></a>

### Devices and locations

*Adding devices, access tokens, online status and locations on a map.*

<a id="devices--add"></a>

#### Adding a device

Devices → *Add device*. Only the name is required:

|             |                                                                                                                                                           |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Name        | How the device is shown everywhere, e.g. "Greenhouse node".                                                                                               |
| Type        | Free text such as "temperature sensor" or "power meter".                                                                                                  |
| Location    | One of your locations (see [Locations](#devices--locations)).                                                                                             |
| Board       | The microcontroller, e.g. ESP32 or ESP32-S3. Firmware updates use it to refuse firmware built for another board. The device can also report it by itself. |
| Description | Notes for yourself and the people you share the device with.                                                                                              |
| Active      | An inactive device is kept with all its data, but the platform rejects new data from it.                                                                  |

<a id="devices--token"></a>

#### Access token

Every device gets a secret **access token**, shown in the *Connect a device* section of the device page. The device puts it in every request (HTTP and CoAP) or uses it as its MQTT username and password.

  - Treat it like a password: anyone who has it can send data as this device.
  - *Generate new token* replaces it at once. The old token stops working immediately, so update the device afterwards.
  - Only the owner and users with the Editor role can see the token ([Sharing devices](#sharing)).

<a id="devices--device-page"></a>

#### The device page

Open a device from the device list or the dashboard. From top to bottom you find:

  - **Status**: Online/Offline, the time of the last data and the protocol it came through.
  - **Active alarms** of this device.
  - **Controls** (actuators) and **Sensors**, with their latest values ([Controlling devices](#control)).
  - **Firmware**: running version, board, MAC address and over-the-air updates ([Firmware updates](#firmware-updates)).
  - **Connect a device**: the access token, plus addresses, topics and ready-to-copy examples for HTTP, MQTT and CoAP.
  - **Commands** sent to the device and their results.
  - **State machines** the device takes part in, **Sharing** and **Connection history**.

<a id="devices--status"></a>

#### Online and offline

A device is **online** while it sends data, and becomes **offline** after 300 seconds without data. An MQTT device that announces its session (see [status topic](#device-api--mqtt-status-topic)) stays online while the connection is open, even without data, and goes offline at once when the connection is lost. Every change is listed under *Connection history* with its reason and also appears in the activity log.

<a id="devices--locations"></a>

#### Locations

Locations → *Add location*. Type the latitude and longitude, or click the map (you can drag the marker). Then choose the location when you add or edit a device. The dashboard groups devices by location, and the location page shows its devices on a map. A location that is still used by a device cannot be deleted: move those devices first.

<a id="devices--delete"></a>

#### Deleting a device

*Delete* on the device page removes the device with all its variables and data. Only the owner (or an administrator) can delete a device. If you only want to stop it from sending, make it inactive instead.

<a id="variables"></a>

### Variables

*Sensors and actuators of a device: keys, units, value types and limits.*

A variable is one value of a device. Its **key** is the name the device uses in its data, e.g. `temperature` in `{"temperature": 25.1}`.

<a id="variables--automatic"></a>

#### Created automatically

When a device sends a key that does not exist yet, the variable is created automatically as a sensor with that key. You can then edit it to add a label and a unit. Add variables yourself when you want to choose the settings first, and always for actuators.

<a id="variables--fields"></a>

#### Settings of a variable

|                  |                                                                                                                                           |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Key              | Letters, digits and underscores, starting with a letter, at most 50 characters, e.g. `soil_moisture`. No spaces. Keys are case sensitive. |
| Label            | Display name, e.g. "Soil moisture". Blank = the key is shown.                                                                             |
| Unit             | e.g. °C, %, V. May be blank.                                                                                                              |
| Kind             | **Sensor** (read only) or **Actuator** (can be controlled from the platform).                                                             |
| Value type       | **Number**, or **On / off** (stored as 1 and 0, shown as ON and OFF).                                                                     |
| Minimum, maximum | For number actuators: the range a user may request, e.g. 0–180 for a servo. Blank = no limit.                                             |

<a id="variables--values"></a>

#### What a device may send

  - Values must be numbers. `true` and `false` are accepted and stored as 1 and 0. Text values are rejected.
  - One message may contain many keys, and several readings with their own time ([payload formats](#device-api--payload)).
  - An actuator reports its real state with the same key, e.g. the pump sends `{"pump": 1}` after switching on.

<a id="variables--delete"></a>

#### Deleting a variable

*Delete* on the variable page removes the variable and all its data. If the device keeps sending that key, the variable is created again.

<a id="dashboard"></a>

### Dashboard, charts and history

*Following data live, charts with time ranges and zoom, and downloading data.*

<a id="dashboard--dashboard"></a>

#### The dashboard

The dashboard shows every variable of your devices (and of devices shared with you), grouped by location and device, with the latest value and how long ago it arrived. The numbers at the top count locations, devices, devices online now and variables shown. A banner appears when alarms are waiting for acknowledgement.

**Customize** chooses what appears: switch off whole locations, devices or single variables. These settings only affect your own dashboard.

<a id="dashboard--live"></a>

#### Live values

New values appear without reloading the page. The switch *Live updates* shows the connection state: *Live* means changes arrive instantly; otherwise the page checks for new data every few seconds. Updates pause while you are typing in a form.

<a id="dashboard--chart"></a>

#### Charts

Click a variable to open its page with a chart and the data history.

  - **Time range**: the last 5 minutes, 10 minutes, 30 minutes, 1 hour, 1 day (default), 1 week, 1 month or 1 year, or *Custom range…* with a start and end. Leave the end empty to follow the current time.
  - **Zoom**: drag across the chart to zoom into a time range. Hold Ctrl and scroll to zoom, or hold Ctrl and drag to move left and right. On a touch screen, pinch to zoom. *Reset* returns to the chosen range.
  - For long ranges the chart shows the **average per minute or per hour** instead of every reading, so it stays fast. On/off variables show the highest value per period, so a short ON is not lost.
  - If the range has no data, the page tells you when the latest reading was; choose a longer range.

<a id="dashboard--history"></a>

#### Data history and download

Below the chart, *Data history* lists every reading, newest first, page by page. *Download* saves the data as **CSV** or **Excel (XLSX)**, either for the range of the chart or all data of the variable.

Actuator pages also show the *Request history*: every value requested by users and rules, with the time and who requested it.

<a id="control"></a>

### Controlling devices

*Switching actuators with requested states, and sending commands.*

There are two ways to make a device do something. Use a **requested state** for things that have a state (a relay, a fan, a servo, a set point) and a **command** for one-off actions (reboot, open a valve for 5 seconds).

|                        | Requested state                                             | Command                                            |
| ---------------------- | ----------------------------------------------------------- | -------------------------------------------------- |
| Examples               | pump ON/OFF, fan speed 60, servo 90°                        | reboot, blink, open valve for N seconds            |
| Device offline         | the request waits and is applied when the device comes back | the command expires if it is not picked up in time |
| Clicking several times | only the latest value counts                                | every click is a new command                       |
| Proof it worked        | the device reports the same value                           | status and response of the command                 |

<a id="control--requested-state"></a>

#### Switching an actuator

Actuator variables show a switch (on/off) or a number field with *Set* on the dashboard and the device page. The platform stores your request and passes it to the device; the device then reports its real state with the same key. The badge next to the actuator compares both:

|                    |                                                                                    |
| ------------------ | ---------------------------------------------------------------------------------- |
| Waiting for device | The device has not reported anything since the latest request (it may be offline). |
| In sync            | The device reported the requested value.                                           |
| Mismatch           | The device reported, but with another value (stuck, refused, or changed locally).  |

Number actuators only accept values within their minimum and maximum. Switching needs at least the Operator role on a shared device. How a device receives requested states is described in the [MQTT](#device-api--mqtt) and [CoAP](#device-api--coap) references. HTTP devices can only send data.

<a id="control--commands"></a>

#### Sending a command

Device page → *Commands*:

1.  **Method**: the name of the action, e.g. `reboot` or `blink`. The device firmware decides which methods exist.
2.  **Parameters**: JSON for the action, e.g. `{"times": 5}`. Use `{}` for none.
3.  **Expires after**: 1 minute, 5 minutes, 1 hour or 1 day. A command that has not finished before it expires is never run later.

The table below the form shows each command with its status and the device's response:

|           |                                                     |
| --------- | --------------------------------------------------- |
| Queued    | waiting for the device                              |
| Sent      | delivered to the device, no answer yet              |
| Succeeded | the device answered with success                    |
| Failed    | the device answered with an error, see the response |
| Expired   | not finished in time; it will not run any more      |

Rules can also switch actuators and send commands automatically ([Rules overview](#rules)).

## Connecting devices

<a id="connect-esp32"></a>

### Connect an ESP32

*Step-by-step guide with ready-made firmware templates for HTTP, MQTT and CoAP.*

This chapter takes you from an ESP32 board to live data on the platform. The firmware templates below are ready to use: the server address, port, topic prefix and root certificate are already filled in, and when you pick a device, its access token too.

On the website (https://iot.reslab-csub.cloud/docs/connect-esp32/), log in and pick one of your devices: its access token is then filled in everywhere in this chapter and in the firmware templates.

<a id="connect-esp32--overview"></a>

#### Overview

1.  **Create the device** on the Devices page. Sensor variables are created automatically from the data; add actuator variables (e.g. `lamp`, kind "Actuator", "On / off") yourself if the device should be controlled.
2.  **Prepare Arduino IDE** with the ESP32 board package and the libraries of your protocol.
3.  **Choose HTTP, MQTT or CoAP.**
4.  **Download the firmware template** and fill in your Wi-Fi name and password.
5.  **Upload** it to the ESP32 over USB and open Serial Monitor at 115200 baud.
6.  **Check the device page**: "Last data" changes every few seconds, and the Firmware card shows the running version, the board and the [MAC address](#firmware-updates--mac).

<a id="connect-esp32--arduino-ide"></a>

#### Prepare Arduino IDE

1.  Install **Arduino IDE 2** from [arduino.cc/en/software](https://www.arduino.cc/en/software).
2.  *Tools → Board → Boards Manager*: search **esp32** and install **"esp32 by Espressif Systems"**. If it is not listed, add this URL in *File → Preferences → Additional boards manager URLs*: `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
3.  *Tools → Board*: choose your board, e.g. **ESP32 Dev Module** (or ESP32-S3 / C3 Dev Module).
4.  *Tools → Partition Scheme*: keep a scheme with OTA support, e.g. **Default 4MB with spiffs**, so the device can later be [updated over the air](#firmware-updates).
5.  *Tools → Port*: the USB port of the board. No port? Install the USB-to-serial driver of the chip on your board (**CP210x** or **CH340**) and use a data cable, not a charge-only cable.
6.  *Sketch → Include Library → Manage Libraries*: install the libraries of your protocol.

| Protocol | Libraries to install                                                    |
| -------- | ----------------------------------------------------------------------- |
| HTTP     | none, everything comes with the ESP32 board package                     |
| MQTT     | PubSubClient (Nick O'Leary), ArduinoJson 7 (Benoit Blanchon)            |
| CoAP     | CoAP simple library (Hirotaka Niisato), ArduinoJson 7 (Benoit Blanchon) |

<a id="connect-esp32--protocol"></a>

#### Choose a protocol

|                           | HTTP                       | MQTT                                                    | CoAP                                    |
| ------------------------- | -------------------------- | ------------------------------------------------------- | --------------------------------------- |
| How it works              | one request per reading    | one permanent connection to a broker, publish/subscribe | small UDP messages, request/response    |
| Direction                 | device → platform          | both ways, instant                                      | both ways (the template asks every 2 s) |
| Actuators and commands    | no                         | yes                                                     | yes                                     |
| Firmware updates          | no                         | yes                                                     | not in this template                    |
| Encryption on this server | yes (https)                | yes (TLS)                                               | no                                      |
| Good for                  | a first test, sensors only | most projects, especially over the internet             | trusted local networks, battery devices |

Not sure? Start with **HTTP** to check the Wi-Fi and the token, then move to **MQTT**.

<a id="connect-esp32--http"></a>

#### HTTP template

Sends telemetry and its firmware/MAC report with HTTP POST requests. Sketch: `esp32_telemetry.ino`.

|                   |                                                                          |
| ----------------- | ------------------------------------------------------------------------ |
| Server (`SERVER`) | `https://iot.reslab-csub.cloud`                                          |
| Telemetry         | `POST https://iot.reslab-csub.cloud/api/v1/<token>/telemetry`            |
| Firmware and MAC  | `POST https://iot.reslab-csub.cloud/api/v1/<token>/firmware`             |
| Encryption        | yes: the sketch checks the server against [ROOT\_CA](#security--root-ca) |

  - The template sends random temperature and humidity every 10 seconds. Replace `readTemperature()` and `readHumidity()` with real sensor readings, e.g. a DHT22.
  - Serial Monitor shows each request and its status: `200` saved, `400` wrong JSON, `401` wrong token, `403` device inactive, `301` the server only accepts `https://`, a negative number means no connection.

**Download:** the HTTP firmware template (.zip) is on the website, chapter *Connect an ESP32* (https://iot.reslab-csub.cloud/docs/connect-esp32/); log in first.

<a id="connect-esp32--mqtt"></a>

#### MQTT template

Two-way: telemetry, actuator states, commands, online/offline status and OTA updates. Sketch: `esp32_mqtt.ino`.

|                               |                                                 |
| ----------------------------- | ----------------------------------------------- |
| Broker (`MQTT_HOST`)          | `iot.reslab-csub.cloud`                         |
| Port (`MQTT_PORT`)            | `8883`                                          |
| Encryption (`USE_TLS`)        | `1`, see [root certificate](#security--root-ca) |
| Topic prefix (`TOPIC_PREFIX`) | `<prefix>`                                      |
| Username / password           | the access token, for both                      |
| Telemetry topic               | `<prefix>/<token>/telemetry`                    |

  - The built-in LED follows the actuator `lamp`: add a variable with key `lamp`, kind "Actuator", value type "On / off", then switch it on the device page. The commands `blink` and `ping` also work.
  - A new device, or a new token, can log in to the broker after about 2 minutes.
  - Serial Monitor prints `failed (state 5)` when the broker rejects the login (wrong token), and `state -2` when it cannot reach the broker (address, port, or [certificate](#security--root-ca)).
  - All topics are listed in the [device API reference](#device-api--mqtt).

**Download:** the MQTT firmware template (.zip) is on the website, chapter *Connect an ESP32* (https://iot.reslab-csub.cloud/docs/connect-esp32/); log in first.

<a id="connect-esp32--coap"></a>

#### CoAP template

Two-way over UDP: telemetry, actuator states and commands, firmware/MAC report. Sketch: `esp32_coap.ino`.

|                        |                                                                   |
| ---------------------- | ----------------------------------------------------------------- |
| Server (`SERVER_HOST`) | `iot.reslab-csub.cloud`                                           |
| Port (`SERVER_PORT`)   | `5683` (UDP)                                                      |
| Telemetry              | `POST coap://iot.reslab-csub.cloud:5683/api/v1/<token>/telemetry` |

  - There is no broker: the ESP32 sends UDP packets straight to the server, so UDP port 5683 must be reachable from the ESP32's network.
  - The built-in LED follows the actuator `pump` (change `ACTUATOR_KEY` for another key), and the commands `blink` and `ping` work.
  - CoAP traffic is **not encrypted**. Use it only on networks you trust.

**Download:** the CoAP firmware template (.zip) is on the website, chapter *Connect an ESP32* (https://iot.reslab-csub.cloud/docs/connect-esp32/); log in first.

<a id="connect-esp32--after-upload"></a>

#### After uploading

1.  Serial Monitor shows the Wi-Fi connection, the MAC address and every message the board sends.
2.  On the device page, *Last data* changes and the status becomes *Online*. New keys appear as variables.
3.  The Firmware card shows version `1.0.0`, the board and the MAC address.
4.  Something wrong? See [Troubleshooting](#troubleshooting--device).

<a id="device-api"></a>

### Device API reference

*Every address, topic and payload a device can use, with examples to try from a computer.*

Everything a device can send and receive. The examples use this server's addresses; `<token>` stands for the device's access token (copy it from the device page). The device page shows the same examples with the token filled in.

<a id="device-api--payload"></a>

#### Telemetry payload

All three protocols accept the same JSON:

| Form               | Example                                                                            | Time of the reading                                 |
| ------------------ | ---------------------------------------------------------------------------------- | --------------------------------------------------- |
| Values only        | `{"temperature": 25.1, "humidity": 60}`                                            | when the platform receives it                       |
| Values with a time | `{"ts": 1791436800000, "values": {"temperature": 25.1}}`                           | `ts` in **milliseconds** since 1 January 1970 (UTC) |
| A list of either   | `[{"ts": 1791436800000, "values": {...}}, {"ts": 1791436860000, "values": {...}}]` | per item, e.g. readings stored while Wi-Fi was down |

  - Keys: letters, digits and underscores, starting with a letter, at most 50 characters. New keys become new variables.
  - Values: numbers; `true`/`false` are stored as 1/0. Text is rejected.
  - At most 1000 values per message. A time in seconds instead of milliseconds, or more than 5 minutes in the future, is rejected.
  - A message is saved completely or rejected completely, never partly.

<a id="device-api--http"></a>

#### HTTP

| Purpose                                                   | Request                                                       |
| --------------------------------------------------------- | ------------------------------------------------------------- |
| Send telemetry                                            | `POST https://iot.reslab-csub.cloud/api/v1/<token>/telemetry` |
| Firmware report (version, board, MAC) and update progress | `POST https://iot.reslab-csub.cloud/api/v1/<token>/firmware`  |

Send the JSON as the body with the header `Content-Type: application/json`. Answers:

|     |                                                                                           |
| --- | ----------------------------------------------------------------------------------------- |
| 200 | saved; the answer lists new variables, e.g. `{"saved": 2, "new_variables": ["humidity"]}` |
| 400 | the body is not valid JSON or breaks a rule above; `{"error": "..."}` explains why        |
| 401 | unknown access token                                                                      |
| 403 | the device is inactive                                                                    |
| 405 | use POST                                                                                  |

Try it from a computer:

    curl -X POST https://iot.reslab-csub.cloud/api/v1/<token>/telemetry -H "Content-Type: application/json" -d '{"temperature": 25.1}'

HTTP only carries data from the device. To receive requested states and commands, use MQTT or CoAP.

<a id="device-api--mqtt"></a>

#### MQTT

|                     |                                                                                         |
| ------------------- | --------------------------------------------------------------------------------------- |
| Broker              | `iot.reslab-csub.cloud`, port `8883`, TLS (see [certificates](#security--root-ca))      |
| Username / password | the access token, for both. Each device may only use topics that contain its own token. |
| WebSocket           | `wss://iot.reslab-csub.cloud/mqtt` for browsers and web apps (same account and topics)  |

| Direction         | Topic                                     | Payload                                                                                                                                                |
| ----------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Device publishes  | `<prefix>/<token>/telemetry`              | telemetry, see [payload](#device-api--payload)                                                                                                         |
| Device subscribes | `<prefix>/<token>/desired`                | requested states of all actuators, e.g. `{"lamp": 1, "fan_speed": 40}`; kept by the broker (retained), so a device receives it right after subscribing |
| Device subscribes | `<prefix>/<token>/commands/+`             | `{"id": 12, "method": "blink", "params": {"times": 5}}`; not retained                                                                                  |
| Device publishes  | `<prefix>/<token>/commands/<id>/response` | `{"success": true, ...}` or `{"success": false, "error": "..."}`                                                                                       |
| Device subscribes | `<prefix>/<token>/errors`                 | why a message from the device was rejected: `{"error": "...", "topic": "..."}`                                                                         |
| Device publishes  | `<prefix>/<token>/firmware`               | firmware report and update progress ([firmware report](#firmware-updates--report))                                                                     |
| Device publishes  | `<prefix>/<token>/status`                 | `online` after connecting and `offline` as Last Will, both retained (optional)                                                                         |

Use QoS 1 for telemetry, desired states, commands and responses. After switching an actuator, publish its new state as telemetry with the same key, e.g. `{"lamp": 1}`, so the platform can show *In sync*.

<a id="device-api--mqtt-status-topic"></a>

##### Online status with Last Will

Connect with a Last Will message `offline` (retained, QoS 1) on the status topic, then publish `online` (retained) there. The device then counts as online while the connection is open, and as offline as soon as the broker notices the connection is gone, instead of after 300 seconds without data.

<a id="device-api--mqtt-tools"></a>

##### Trying MQTT from a computer

With the Mosquitto command-line tools (or a graphical client such as MQTTX):

    mosquitto_pub -h iot.reslab-csub.cloud -p 8883 --tls-use-os-certs -u <token> -P <token> -t "<prefix>/<token>/telemetry" -m '{"temperature": 25.1}'

    mosquitto_sub -h iot.reslab-csub.cloud -p 8883 --tls-use-os-certs -u <token> -P <token> -t "<prefix>/<token>/desired" -t "<prefix>/<token>/commands/+" -t "<prefix>/<token>/errors" -v

<a id="device-api--coap"></a>

#### CoAP

Server `coap://iot.reslab-csub.cloud:5683` (UDP, not encrypted). All resources start with `api/v1/<token>`:

| Method          | Resource                                                                  | Purpose                                                                               |
| --------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| POST            | `coap://iot.reslab-csub.cloud:5683/api/v1/<token>/telemetry`              | telemetry, see [payload](#device-api--payload); answer `2.04 Changed`                 |
| GET (+ Observe) | `coap://iot.reslab-csub.cloud:5683/api/v1/<token>/desired`                | requested states, e.g. `{"pump": 1}`; with Observe the server sends changes by itself |
| GET (+ Observe) | `coap://iot.reslab-csub.cloud:5683/api/v1/<token>/commands`               | open commands: `{"commands": [{"id", "method", "params"}]}`                           |
| POST            | `coap://iot.reslab-csub.cloud:5683/api/v1/<token>/commands/<id>/response` | `{"success": true, ...}`                                                              |
| POST            | `coap://iot.reslab-csub.cloud:5683/api/v1/<token>/firmware`               | firmware report and update progress                                                   |

Error answers use CoAP codes: `4.00` wrong payload, `4.01` unknown token, `4.03` device inactive, `4.04` unknown resource. A device that uses Observe should send something at least every few minutes, so routers keep the UDP path open, and should observe again if notifications stop.

Try it with `coap-client` from the libcoap tools:

    coap-client -m post -t json -e '{"temperature": 25.1}' coap://iot.reslab-csub.cloud:5683/api/v1/<token>/telemetry

    coap-client -m get -s 60 coap://iot.reslab-csub.cloud:5683/api/v1/<token>/desired

<a id="security"></a>

### Security and certificates

*Access tokens, encryption, and the root certificate an ESP32 needs.*

<a id="security--token"></a>

#### Access tokens

  - The access token is the only thing a device needs to send data as that device. Keep it out of public code repositories and screenshots.
  - If a token may have leaked, click *Generate new token* on the device page. The old token stops working at once; update the device with the new one.
  - Only the owner and Editors of a device can see its token.
  - Firmware update downloads use separate, single-use links, so seeing an update command does not reveal the access token.

<a id="security--encryption"></a>

#### Encryption on this server

|      |                                                          |
| ---- | -------------------------------------------------------- |
| HTTP | https encrypted                                          |
| MQTT | TLS encrypted, port 8883                                 |
| CoAP | no DTLS not encrypted: use it only on networks you trust |

Without encryption, anyone on the same network path can read the data and the access token. Prefer the encrypted protocols for devices that connect over the internet.

<a id="security--root-ca"></a>

#### The root certificate an ESP32 needs

With TLS (`https://` or encrypted MQTT) the server proves its identity with a certificate. The ESP32 checks that proof against a **root certificate** (root CA) that it carries in its firmware (`ROOT_CA` in the templates). This stops another machine from pretending to be the server. The device does **not** need a certificate of its own: it logs in with its access token inside the encrypted connection.

|                     |                                                                                                   |
| ------------------- | ------------------------------------------------------------------------------------------------- |
| Root certificate    | ISRG Root X1 (Let's Encrypt), valid until 4 June 2035                                             |
| SHA-256 fingerprint | `96:BC:EC:06:26:49:76:F3:74:60:77:9A:CF:28:C5:A7:CF:E8:A3:C0:AA:E1:1A:8F:FC:EE:05:C0:BD:DF:08:C6` |

[Download root certificate (PEM)](https://iot.reslab-csub.cloud/devices/connect/isrg-root-x1.pem) The firmware templates already contain it; you need the file only for your own firmware. Downloading requires logging in.

<a id="security--own-firmware"></a>

##### Using it in your own firmware

Paste the whole PEM text, including the `-----BEGIN CERTIFICATE-----` and `-----END CERTIFICATE-----` lines, into your sketch and pass it to the TLS client:

    const char* ROOT_CA = R"PEM(
    -----BEGIN CERTIFICATE-----
    ...
    -----END CERTIFICATE-----
    )PEM";
    
    WiFiClientSecure client;
    client.setCACert(ROOT_CA);

It is also published at [letsencrypt.org/certificates](https://letsencrypt.org/certificates/) ("Root Certificates", ISRG Root X1, PEM format).

<a id="security--other-server"></a>

##### Checking which root a server uses

To find out which certificate authority signed a server's certificate, run on a computer with OpenSSL:

    openssl s_client -connect iot.reslab-csub.cloud:8883 -showcerts </dev/null | openssl x509 -noout -issuer

The issuer printed is an intermediate certificate; the root is the one that signed it (shown as `i:` on the last certificate of `-showcerts`). Put the **root** in your firmware, not the intermediate: intermediates are replaced every few months, roots last for years. Renewing the server's own certificate does not affect devices as long as the root stays the same.

<a id="security--not-supported"></a>

#### Not supported

  - **Client certificates per device** (X.509, mutual TLS): devices are identified by their access token only.
  - **CoAP over DTLS** (`coaps://`).

<a id="firmware-updates"></a>

### Firmware updates and MAC address

*Updating devices over the air, and the firmware report with version, board and MAC address.*

A device running the MQTT firmware template can be updated over the air (OTA): you upload a compiled firmware file once, then update any compatible device from its page. The device downloads the file, checks it, restarts into it and reports the new version.

<a id="firmware-updates--prepare"></a>

#### Preparing a device

  - Use the [MQTT firmware template](#connect-esp32--mqtt) (or firmware that implements the same `ota` command) and upload it once over USB.
  - In Arduino IDE choose a partition scheme with OTA support, e.g. *Default 4MB with spiffs*.
  - Set the board of the device (device → *Edit*), or let the device report it.

<a id="firmware-updates--upload"></a>

#### Uploading firmware

1.  Change `FW_VERSION` in the sketch, e.g. from `"1.0.0"` to `"1.1.0"`.
2.  Arduino IDE: *Sketch → Export Compiled Binary*. This creates a `.bin` file in the sketch folder.
3.  Firmware → *Upload firmware*: title, **version exactly as in `FW_VERSION`**, board, the .bin file, and release notes if you like. Files up to 4.0 MB are accepted; the platform computes the MD5 and SHA-256 checksums.

The version must match what the new firmware reports after it starts: that report is how the platform knows the update worked. Firmware you upload is visible only to you (administrators see all).

<a id="firmware-updates--update"></a>

#### Updating a device

Device page → *Firmware* card → choose the version under *Update to* → *Update firmware*. Only firmware for the device's board is offered. The device must be online. Progress appears in the card by itself:

|                      |                                                                        |
| -------------------- | ---------------------------------------------------------------------- |
| Queued               | waiting for the device to pick up the update command                   |
| Downloading          | the device downloads the file (with percentage)                        |
| Downloaded, Verified | the file is complete and its MD5 matches                               |
| Updating             | the device restarts into the new firmware                              |
| Updated              | the device started and reported the new version                        |
| Failed               | see the reason in the update's details; the old firmware keeps running |
| Canceled             | you clicked *Cancel*                                                   |

Common reasons for *Failed*: the device was offline and did not pick up the command in time; the download broke or the checksum did not match; the new firmware did not start and the device went back to the old version; or nothing was heard from the device for a long time. Starting and canceling updates needs the Editor role on a shared device. The Firmware page lists recent updates of all your devices.

<a id="firmware-updates--report"></a>

#### The firmware report

Every template reports, when it starts (MQTT: on every connection), which firmware it runs and on which board:

    {"version": "1.0.0", "board": "esp32", "mac": "24:6F:28:AA:BB:CC"}

| Protocol | Send it to                                                       |
| -------- | ---------------------------------------------------------------- |
| HTTP     | `POST https://iot.reslab-csub.cloud/api/v1/<token>/firmware`     |
| MQTT     | topic `<prefix>/<token>/firmware`                                |
| CoAP     | `POST coap://iot.reslab-csub.cloud:5683/api/v1/<token>/firmware` |

`board` is one of: `esp32`, `esp32s2`, `esp32s3`, `esp32c3`, `esp8266`, `sim`. The version may contain letters, digits and `. _ + -`.

<a id="firmware-updates--mac"></a>

#### MAC address

The report also carries the board's Wi-Fi **MAC address**. The platform stores it with the device and shows it in the Firmware card. It tells you which physical board is using the token, for example to find the board in your router's list of connected devices, or to notice that a token was copied to another board (the MAC address changes).

  - On the ESP32 the value comes from `WiFi.macAddress()`, after Wi-Fi has started.
  - Accepted forms: `24:6F:28:AA:BB:CC`, `24-6f-28-aa-bb-cc`, `246f.28aa.bbcc` or `246F28AABBCC`. It is stored as `24:6F:28:AA:BB:CC`.
  - An invalid MAC address rejects the whole report, so nothing from it is saved.
  - Reports without `mac` are still accepted. The card then shows "unknown" until the device sends its MAC address.

## Automation

<a id="rules"></a>

### Rules overview

*Rule models, the rule list, execution log, import and export.*

Rules react to device data automatically, e.g. "when soil moisture drops below 20 %, switch the pump on". Rules → *New rule* starts by choosing a model.

<a id="rules--models"></a>

#### Which model should I use?

| Model                                      | Remembers                                               | Good for                                                            | Example                                                        |
| ------------------------------------------ | ------------------------------------------------------- | ------------------------------------------------------------------- | -------------------------------------------------------------- |
| [ECA](#rules-eca) (Event–Condition–Action) | one state per rule: Idle, Pending or Active             | threshold alarms and simple automations, filled in as a form        | if soil \< 20 for 60 s then pump ON; when soil \> 30, pump OFF |
| [Rule chain](#rule-chains)                 | almost nothing                                          | processing every message with branches, extra data and calculations | Input → temperature \> 32? → add humidity → fan ON → email     |
| [State machine](#state-machines)           | a named state per device and how long it has been there | alarms with stages, device modes, life cycles                       | NORMAL → WARNING → CRITICAL → NORMAL                           |

Models marked *Coming soon* on the model page cannot be used yet. Rules of any model can also run on a [schedule](#schedules) (ECA and rule chains).

<a id="rules--actions"></a>

#### What rules can do

|                       |                                                                                                                             |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Set actuator          | request a new state, e.g. pump = 1 (ON). Skipped if that value is already requested.                                        |
| Send command          | queue a command with method, parameters and expiry, like sending it by hand.                                                |
| Raise alarm           | create an alarm with a severity and message; it clears by itself when the rule recovers ([Alarms](#alarms-notifications)).  |
| Send email            | to the addresses you enter, or to the rule owner when left blank.                                                           |
| Send Telegram message | to the chat connected in the rule owner's profile, or to the chats you enter ([Telegram](#alarms-notifications--telegram)). |

A rule may use the sensors of any device you can see, but it can only switch, command or raise alarms on devices you may control (your own devices, or shared ones with at least the Operator role). Access is checked again every time a rule runs.

<a id="rules--placeholders"></a>

##### Placeholders in messages

Alarm, email and Telegram messages can include values from the moment the rule ran:

|                                         |                                                                          |
| --------------------------------------- | ------------------------------------------------------------------------ |
| `{rule}`                                | name of the rule                                                         |
| `{device}`, `{variable}`, `{value}`     | the device, variable and value that triggered the rule                   |
| `{threshold}`                           | the threshold of the condition (ECA)                                     |
| `{values}`                              | all values in the condition, e.g. `Soil moisture = 15, Temperature = 32` |
| `{transition}`                          | activated or cleared                                                     |
| `{time}`                                | the scheduled time (scheduled rules)                                     |
| `{temperature}`, …                      | rule chains: any key in the message                                      |
| `{state}`, `{from_state}`, `{to_state}` | state machines                                                           |

<a id="rules--list"></a>

#### The rule list

Filter by model and state (*Active now*, *Enabled*, *Disabled*, *On a schedule*) and search by any word in the rule: a device name, a variable key such as `temperature`, or an action such as `email`.

<a id="rules--rule-page"></a>

#### The rule page

  - **Enable / Disable** switches the rule on or off. Saving, enabling or disabling resets what the rule remembers (an ECA rule returns to Idle, a state machine puts every device back in its initial state, without running actions).
  - **State** shows what the rule is doing now and the last values it looked at.
  - **Execution log** lists every time the rule did something, the values at that moment and the result of each action (✓ done, ✗ failed, ⏭ skipped).
  - **Alarms from this rule.**

<a id="rules--import-export"></a>

#### Export and import

  - **Export** on the rule page downloads the rule as JSON. The *JSON* card shows the same text to copy.
  - **Import** on the rule list creates a new rule from JSON; the model follows `"model"` in the file.
  - **Import** on a rule page replaces that rule's content; its history and alarms are kept.

The JSON uses device and variable **names**, not internal numbers, so a rule can be copied to another account with devices of the same names. Files up to 100 KB are accepted. If anything in the file is wrong, nothing is saved and the errors are listed.

<a id="rules-eca"></a>

### ECA rules

*When an event arrives, if a condition holds, then run actions; with hysteresis and timing.*

An ECA rule is one form: **When** an event arrives, **if** the condition holds, **then** run actions. When the condition recovers, the rule **clears** and can run other actions.

    WHEN  new data for Soil moisture or Temperature
    IF    (Soil moisture < 20 AND Temperature > 30) OR Soil moisture < 10   for at least 60 s
    THEN  set Water pump to ON; raise a major alarm                   (when the rule activates)
    CLEAR when Soil moisture > 30
    THEN  set Water pump to OFF                                       (when the rule clears; the alarm clears by itself)

<a id="rules-eca--event"></a>

#### 1\. When: the event

|                                           |                                                                                                                                                             |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| New data for a variable in the conditions | The rule is checked every time any variable used in the conditions sends a value. Values of the other variables are taken from their latest stored reading. |
| Device sends no data                      | Choose a device and a number of seconds. The rule activates when the device has been quiet that long, and clears when data arrives again.                   |
| On a schedule                             | The rule is checked at set times, not when data arrives ([Schedules](#schedules)).                                                                          |

<a id="rules-eca--condition"></a>

#### 2\. If: the condition

Conditions compare a variable with a number: `>`, `≥`, `<`, `≤`, `=` or `≠`. For on/off variables use 1 (ON) and 0 (OFF). Conditions are arranged in boxes: **every** condition inside a box must be true (AND), and the rule activates when **any** box is true (OR).

A value counts as *unknown* when it is older than *ignore values older than* (see below). A condition with an unknown value is neither true nor false, so the rule does not activate on stale data.

<a id="rules-eca--clear"></a>

#### Clear when (hysteresis)

Optional. Without it, the rule clears as soon as the condition is false. With a separate clear condition, the rule stays active until that condition holds, which prevents rapid ON/OFF switching around a threshold: e.g. activate below 20, clear above 30. For a rule with one variable, the form rejects clear conditions that overlap the activate condition (e.g. activate below 20, clear above 15), and the rule never activates while its clear condition is also true.

<a id="rules-eca--timing"></a>

#### Timing and safeguards

|                                    |                                                                                                                |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| For at least (seconds)             | The condition must hold this long before the rule activates. Meanwhile the rule is *Pending*. 0 = immediately. |
| Cooldown (seconds)                 | Minimum time between two activations. 0 = no cooldown.                                                         |
| Ignore values older than (seconds) | A variable whose latest value is older than this counts as unknown. 0 = no limit. Default 300.                 |

<a id="rules-eca--actions"></a>

#### 3\. Then: the actions

Add as many actions as you need and choose for each whether it runs **when the rule activates** or **when the rule clears**. The available actions and message placeholders are listed in the [rules overview](#rules--actions). A raised alarm is cleared automatically when the rule clears.

<a id="rules-eca--states"></a>

#### Idle, Pending, Active

|         |                                                                 |
| ------- | --------------------------------------------------------------- |
| Idle    | the condition does not hold                                     |
| Pending | the condition holds, waiting for *for at least* or the cooldown |
| Active  | the rule has activated and ran its "on activate" actions        |

Actions run only when the rule **changes** state, not on every message. A pump switched on by the rule therefore does not get switched on again with every new reading. The *Last evaluation* box on the rule page shows each condition, the value it used, how old it was, and the result.

<a id="rule-chains"></a>

### Rule chains

*Messages that travel through filter, enrichment, transformation and action nodes.*

In a rule chain every message enters at **Input** and travels from node to node. Each node does one job and ends with a result: **True/False** for filters, **Success/Failure** for the others. The message follows the links with that result. A branch without a matching link simply ends.

    Input → [Check value: temperature > 32] —True→ [Add latest value: humidity] —Success→ [Set actuator: fan ON]
                                                                               —Success→ [Rate limit: 600 s] —True→ [Send email]

<a id="rule-chains--input"></a>

#### 1\. Input

|                    |                                                                                                                                                                                                                                                   |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Telemetry messages | The chain runs once for every message from one device, or from any of your own devices. The message carries the keys the device sent, e.g. `temperature`, `humidity`.                                                                             |
| On a schedule      | The chain runs once at every scheduled time; telemetry does not start it. The message carries the time: `hour`, `minute`, `weekday` (0 = Monday), `day`, `month`. Use *Add latest value* nodes to bring in sensor data ([Schedules](#schedules)). |

<a id="rule-chains--nodes"></a>

#### 2\. Nodes

| Category       | Node                                                                                     | What it does                                                                                                                   | Results                                            |
| -------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------- |
| Filter         | Check value                                                                              | compares a key with a number, e.g. `temperature > 32`                                                                          | True, False, Failure (key missing)                 |
| Filter         | Value changed                                                                            | passes when the value changed by at least the minimum change since the last value that passed (per device); blank = any change | True, False, Failure                               |
| Filter         | Rate limit                                                                               | passes at most one message per N seconds (per device)                                                                          | True, False                                        |
| Enrichment     | Add latest value                                                                         | adds the latest stored value of a variable of any device you can see; optionally fails if that value is too old                | Success, Failure (no data, too old, no access)     |
| Transformation | Calculate                                                                                | `key = a op b` with keys or numbers; op is + − × ÷ min max                                                                     | Success, Failure (missing value, division by zero) |
| Action         | Set actuator, Send command, Create alarm, Clear alarm, Send email, Send Telegram message | see [what rules can do](#rules--actions)                                                                                       | Success, Failure                                   |

<a id="rule-chains--links"></a>

#### 3\. Links

Read each link as "when *from* ends with *label*, send the message to *to*". Two links with the same label send a copy of the message down each branch. Links must not loop back. Nodes that are not connected to Input are drawn dashed in the diagram and never run.

<a id="rule-chains--repeats"></a>

#### Avoiding repeated actions

A rule chain has no Idle/Active state: every message walks the graph from Input, so an action node runs **every time** a message reaches it. Put *Value changed* or *Rate limit* before emails and Telegram messages. *Set actuator* is skipped when the value is already requested, and *Create alarm* does not create a second alarm while the rule's alarm is still active.

<a id="rule-chains--page"></a>

#### The rule page

  - The **diagram** shows nodes and links; *Export PNG* saves it as an image.
  - **Last message** shows the path of the latest message through the chain, node by node, with what happened at each step.
  - The **execution log** lists messages that changed something (an actuator, a command, an alarm, an email) or whose action failed. A message that only found "already ON" is not logged.
  - Saving the rule clears what *Value changed* and *Rate limit* remember.

<a id="state-machines"></a>

### State machines

*Devices that move between named states, with actions on entering and leaving.*

In a state machine every watched device is in **one named state** at a time, e.g. NORMAL, WARNING or CRITICAL. Transitions move a device to another state; actions run when a device enters or leaves a state.

    NORMAL —(temperature > 35)→ WARNING —(temperature > 45)→ CRITICAL —(temperature < 30)→ NORMAL
         * —(device goes offline)→ OFFLINE

<a id="state-machines--devices"></a>

#### 1\. Devices to watch

One device, or every device you own. Conditions name a **key** such as `temperature`, not one variable, so the same machine works for every device that sends that key, and each device has its own current state.

<a id="state-machines--states"></a>

#### 2\. States

Give each state a name, a meaning and a colour (the colour is only for the diagram). Exactly one state is the **initial state**: a device starts there the first time the rule sees it, without running any actions.

<a id="state-machines--transitions"></a>

#### 3\. Transitions

From the device's current state, transitions are checked from top to bottom, and the first whose trigger holds moves the device. `*` as the source means "from any state".

| Trigger                      | Holds when                                                                                                                 |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| When a condition holds       | the condition is true; with "for N s" it must stay true that long                                                          |
| After some time in the state | the device has been N seconds in the source state; a transition back to the same state repeats its actions every N seconds |
| Device sends no data         | no data for N seconds                                                                                                      |
| Device goes offline          | the device's status turns offline                                                                                          |
| Button (manual)              | someone presses the button shown on the rule page for that device (needs the Operator role on the device)                  |

<a id="state-machines--condition-syntax"></a>

##### Writing conditions

Comparisons joined with `and` / `or`; `and` binds first. On/off values may be written as ON and OFF. Examples:

    temperature > 35
    temperature > 35 and humidity < 40 or temperature > 45
    pump = ON

<a id="state-machines--actions"></a>

#### 4\. Actions on entering and leaving

Each state can have actions that run when a device **enters** it and when it **leaves** it. On a move, the old state's leaving actions run first, then the new state's entering actions. Besides the usual actions there is **Clear alarm**, which clears this rule's active alarm on the device (e.g. on entering NORMAL); one alarm per device stays active until it is cleared. Placeholders: `{device}`, `{state}`, `{from_state}`, `{to_state}`, `{values}`, `{value}`, `{time}`.

<a id="state-machines--page"></a>

#### The rule page

  - The **diagram** shows states and transitions with the number of devices in each state.
  - **Devices** lists each device's state, since when, its latest values, pending timers, and the buttons.
  - The **execution log** lists every change of state with the values at that moment and the result of each action.
  - The device page also shows the state machines the device is in.
  - Saving, enabling or disabling the rule puts every device back in the initial state, without running actions.

<a id="schedules"></a>

### Schedules

*Rules that run at set times, every N minutes, or once.*

ECA rules and rule chains can run at set times instead of when data arrives, e.g. "water at 06:00 if the soil is dry" or "email a report every weekday at 07:00".

<a id="schedules--kinds"></a>

#### Three kinds of schedule

| Repeat          | You fill in                                 | Example                                             |
| --------------- | ------------------------------------------- | --------------------------------------------------- |
| At set times    | one or more times, and the days of the week | `06:00, 18:30` on Mon–Fri                           |
| Every N minutes | 1 to 1440 minutes, and the days of the week | every 15 minutes, every day: 00:00, 00:15, 00:30, … |
| Once            | a date and time in the future               | 20 Oct 07:30                                        |

Times use the server's time zone (Asia/Jakarta). The rule page shows the next planned runs and the last scheduled time.

<a id="schedules--eca"></a>

#### Scheduled ECA rules

  - Choose *On a schedule* as the event.
  - The condition is optional. With a condition, the actions run at the scheduled time only if it holds (values come from the latest stored readings); otherwise the run is logged as *Skipped*. Without a condition, the actions run at every scheduled time.
  - **Keep active for (seconds)**: how long the rule stays active before it clears by itself and runs its "on clear" actions, e.g. 600 for "pump ON for 10 minutes". 0 = only the "on activate" actions run (one-shot emails, commands, alarms).
  - If the rule is still active from the previous run when the next time comes, that run is skipped.

<a id="schedules--chains"></a>

#### Scheduled rule chains

Choose *On a schedule* for the Input. At every scheduled time one message enters the chain carrying the time (`hour`, `minute`, `weekday`, `day`, `month`). Add *Add latest value* nodes to bring in sensor readings. Telemetry does not start a scheduled chain.

<a id="schedules--missed"></a>

#### Missed runs and disabled rules

  - A run that cannot start within 5 minutes of its time (for example during maintenance of the platform) is not run late; it is logged as *Skipped* with the reason.
  - A disabled rule does not run. Enabling it plans the next run from that moment.

<a id="alarms-notifications"></a>

### Alarms and notifications

*Alarm life cycle, email and Telegram messages from rules.*

<a id="alarms-notifications--alarms"></a>

#### Alarms

Alarms are raised by rules (*Raise alarm* action or *Create alarm* node) with a severity: **Critical**, **Major**, **Minor** or **Warning**. Each alarm has two independent states:

|                               |                                                                                                                                 |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Active / Cleared              | whether the cause is still there. Rules clear their own alarms when the condition recovers; a person can also *Clear* an alarm. |
| Unacknowledged / Acknowledged | whether someone is handling it. *Acknowledge* records who and when.                                                             |

Active, unacknowledged alarms are shown as a red badge next to *Alarms* in the menu, as a banner on the dashboard, and on the device page. A rule does not create a second alarm while its alarm on that device is still active. Acknowledging and clearing need at least the Operator role on a shared device. The Alarms page shows active alarms, or all alarms with *All*.

<a id="alarms-notifications--email"></a>

#### Email

The *Send email* action sends the message to the addresses you enter (separated by commas), or to the rule owner's email address when left blank. Use [placeholders](#rules--placeholders) to include values.

<a id="alarms-notifications--telegram"></a>

#### Telegram

<a id="alarms-notifications--telegram-connect"></a>

##### Connecting your account

1.  My profile → *Telegram* → *Connect Telegram*.
2.  Click *Open Telegram* and press **START** at the bottom of the chat with the bot.
3.  Come back and press *I pressed Start*. The link works for a few minutes and only for your account; do not share it.
4.  *Send test* checks that messages arrive. *Disconnect* stops them.

<a id="alarms-notifications--telegram-recipients"></a>

##### Recipients

The *Send Telegram message* action sends to the chat connected in the rule owner's profile when *Recipients* is blank. You can also enter chat ids, separated by commas:

|                   |                                                                   |
| ----------------- | ----------------------------------------------------------------- |
| `123456789`       | a private chat                                                    |
| `-1001234567890`  | a group or supergroup; the bot must be a member of the group      |
| `@lab_iot_alerts` | a public channel; the bot must be an administrator of the channel |

In rule chains, put a *Rate limit* or *Value changed* node before a Telegram or email node, otherwise every message sends one.

## Working together

<a id="sharing"></a>

### Sharing devices

*Viewer, Operator and Editor roles, sharing with users and groups.*

Share a device with other users, directly or through a group, and choose what they may do with it.

<a id="sharing--roles"></a>

#### Roles

| They can                                                             | Viewer | Operator | Editor | Owner |
| -------------------------------------------------------------------- | ------ | -------- | ------ | ----- |
| see the device, variables, charts, history, alarms, firmware and log | ✓      | ✓        | ✓      | ✓     |
| use its sensors in the conditions of their own rules                 | ✓      | ✓        | ✓      | ✓     |
| switch actuators, send commands, acknowledge and clear alarms        |        | ✓        | ✓      | ✓     |
| use it as the target of their rules' actions                         |        | ✓        | ✓      | ✓     |
| see the access token and connection details, create a new token      |        |          | ✓      | ✓     |
| change device and variable settings, update firmware                 |        |          | ✓      | ✓     |
| delete the device, decide who has access                             |        |          |        | ✓     |

Someone with access in several ways (directly and through groups) gets the highest of those roles. Administrators can do everything.

<a id="sharing--share"></a>

#### Sharing a device

1.  Open the device page and go to *Sharing* (only the owner sees the form).
2.  Enter a **username**, or choose one of your **groups**, pick a role and click *Share*.
3.  To change a role, choose another role in the list and save, or share again. *Remove* takes the access away.

Users who received a device see it on their dashboard and in their device list, where it is marked "Shared by …" (filter *Shared with me*). They can *Leave* a shared device on its page. The owner sees in the activity log when someone else controls the device.

<a id="sharing--groups"></a>

#### Sharing groups

A group is a list of users, e.g. a class team or the lab technicians. Sharing groups → *New group*, then add members by username. Share a device with the group and every member gets that access; new members get it too, and members who leave lose it. Deleting a group removes the access it gave; the devices themselves are not changed.

<a id="sharing--rules"></a>

#### Shared devices in rules

  - Conditions may use sensors of devices shared with you as Viewer or higher.
  - Actions that switch, command or raise alarms need the Operator role on the target device.
  - "Any of my devices" in a rule chain or "every device I own" in a state machine only includes devices you own.
  - If a share is removed, rules that depended on it stop acting on that device.

<a id="activity-log"></a>

### Activity log

*What happened to your devices, rules and account, with filters.*

The activity log lists what happened to your devices, rules and account, newest first: devices added or changed, actuators switched, commands, rule activations, alarms, firmware updates, sharing changes, logins and more. Telemetry readings are not listed; see each variable's history for those.

<a id="activity-log--columns"></a>

#### What each line shows

  - **Level**: Info, Warning or Error.
  - **Category**: Device, Control, Rule, Alarm, Firmware, Account or Sharing.
  - **Event** with links to the device and rule involved.
  - **By**: the user who did it, or *device*, *rule engine* or *system* for things that happened automatically.

<a id="activity-log--filters"></a>

#### Filters

Search by words, and filter by category, level, device and a time range (start and end). *Clear* removes all filters. Each device page links to the activity of that device. Events on devices shared with you are shown too, but not the private rules of their owners.

## Apps and integrations

<a id="mqtt-sandbox"></a>

### MQTT sandbox

*Practising MQTT on the platform broker without a device.*

The MQTT sandbox lets you practise MQTT (publish, subscribe, wildcards, retained messages, QoS) on the platform's broker without a device. Messages in the sandbox are not stored and do not reach dashboards or rules.

<a id="mqtt-sandbox--create"></a>

#### Creating your sandbox account

1.  My profile → *MQTT sandbox* → *Create sandbox account*.
2.  The card shows the broker address, your sandbox username and password, and your topics. The account works on the broker within about 2 minutes.

The sandbox password is **not** your login password. *New password* replaces it (clients still using the old one are disconnected within about 2 minutes); *Remove sandbox account* deletes the account.

<a id="mqtt-sandbox--topics"></a>

#### Topics you may use

|                             |                                             |
| --------------------------- | ------------------------------------------- |
| `sandbox/<your username>/#` | your own topics, read and write             |
| `sandbox/public/#`          | shared by all sandbox users, read and write |

<a id="mqtt-sandbox--connect"></a>

#### Connecting

Broker `iot.reslab-csub.cloud`, port `8883` with TLS, or `wss://iot.reslab-csub.cloud/mqtt` from a browser. For example:

    mosquitto_sub -h iot.reslab-csub.cloud -p 8883 --tls-use-os-certs -u <sandbox username> -P <sandbox password> -t "sandbox/public/#" -v

Sandbox accounts cannot use device topics. To connect a real device, use its access token instead ([MQTT reference](#device-api--mqtt)).

<a id="connected-apps"></a>

### Connected apps and API

*Signing in to other apps with your account, and the API for app developers.*

Other apps can let you sign in with your IoT Platform account, for example a web MQTT client. They never see your password.

<a id="connected-apps--sign-in"></a>

#### Signing in to another app

1.  In the other app, choose to sign in with IoT Platform. You are sent to this platform (log in if needed, or sign up first).
2.  The page *Allow …?* lists what the app asks for. Click *Allow* to continue or *Cancel* to refuse.

| Permission     | What the app gets                                                                                                                              |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Sign you in    | proof that it is you                                                                                                                           |
| Profile, email | your username and name; your email address                                                                                                     |
| Devices        | your devices and the devices shared with you, including the access tokens of devices you can edit (anyone with a token can act as that device) |
| MQTT sandbox   | your sandbox account, including its password                                                                                                   |

<a id="connected-apps--manage"></a>

#### Managing connected apps

My profile → *Connected apps* → *Manage* lists the apps you allowed, when, and when they were last used. *Revoke access* stops an app at once; it has to ask you again next time.

<a id="connected-apps--api"></a>

#### For app developers

Apps are registered by the platform administrator. Registered apps use OAuth 2.0 with OpenID Connect (authorization code with PKCE) and call the API with the access token they receive:

    curl -H "Authorization: Bearer <access_token>" https://iot.reslab-csub.cloud/api/v2/me/devices

| Request                  | Returns                                                                                                         | Scope          |
| ------------------------ | --------------------------------------------------------------------------------------------------------------- | -------------- |
| `GET /api/v2/me`         | the signed-in user (email only with the `email` scope)                                                          | `profile`      |
| `GET /api/v2/me/devices` | broker details, devices with role, status, firmware, MAC address, variables, and token and topics where allowed | `devices:read` |
| `GET /api/v2/me/sandbox` | the user's MQTT sandbox account                                                                                 | `mqtt:sandbox` |

Answers: `200` success, `401 invalid_token` (missing, wrong, expired or revoked token, or inactive account), `403 insufficient_scope` (the user did not allow that permission). A browser login session does not work on `/api/v2/`. The OpenID Connect discovery document is at `https://iot.reslab-csub.cloud/o/.well-known/openid-configuration`.

## Help

<a id="troubleshooting"></a>

### Troubleshooting

*Common problems and how to solve them.*

<a id="troubleshooting--device"></a>

#### The device does not connect

| What you see                                           | Likely cause and fix                                                                                                                                              |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Serial Monitor shows only dots after "Connecting to …" | Wrong Wi-Fi name or password, or a 5 GHz-only network: the ESP32 supports 2.4 GHz only.                                                                           |
| Strange characters in Serial Monitor                   | Set the baud rate to 115200.                                                                                                                                      |
| No port in Arduino IDE                                 | Install the CP210x or CH340 USB driver and use a data cable.                                                                                                      |
| HTTP status `-1` or MQTT state `-2`                    | The server cannot be reached: wrong address or port, a firewall on your network, or (with encryption) a wrong `ROOT_CA` ([root certificate](#security--root-ca)). |
| HTTP `401`, MQTT state `5`, CoAP `4.01`                | Wrong access token, or the token was regenerated. Download the firmware template again with the device selected.                                                  |
| HTTP `403`                                             | The device is inactive; activate it with *Edit* on the device page.                                                                                               |
| HTTP `301`                                             | The server accepts `https://` only. Change the address to https and fill in `ROOT_CA`.                                                                            |
| HTTP `400`                                             | The JSON is invalid or breaks a rule (text values, keys with spaces, time in seconds). The answer explains which; see [payload](#device-api--payload).            |
| MQTT connects but nothing is saved                     | Wrong topic prefix or token in the topic. Subscribe to the device's `errors` topic to see why messages are rejected.                                              |
| A new device cannot log in to MQTT                     | New devices and new tokens are accepted by the broker after about 2 minutes.                                                                                      |

<a id="troubleshooting--data"></a>

#### Data and status

|                                               |                                                                                                                                                      |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| The device is shown offline although it works | It is offline after 300 seconds without data. Send data more often, or use MQTT with the status topic ([Last Will](#device-api--mqtt-status-topic)). |
| A new key did not appear                      | Keys must start with a letter and contain only letters, digits and underscores. Keys are case sensitive: `Temp` and `temp` are two variables.        |
| The chart is empty                            | No data in the chosen range. The page tells you when the latest reading was; choose a longer range.                                                  |
| Readings appear at the wrong time             | The device sent `ts` with a wrong clock. Leave `ts` out to use the time of arrival.                                                                  |

<a id="troubleshooting--control"></a>

#### Controls and commands

|                                           |                                                                                                                            |
| ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| An actuator stays on *Waiting for device* | The device is offline, uses HTTP (which cannot receive requests), or does not report the new state back with the same key. |
| *Mismatch*                                | The device reported another value than requested: check the firmware and the hardware.                                     |
| The LED of a template does not react      | The actuator variable is missing or has another key than the template (`lamp` for MQTT, `pump` for CoAP).                  |
| Commands expire                           | The device was offline, or its firmware does not handle commands. Choose a longer expiry.                                  |
| Commands fail with "Unknown method"       | The firmware does not implement that method; the templates know `blink` and `ping` (and `ota` for MQTT).                   |

<a id="troubleshooting--rules"></a>

#### Rules and notifications

|                                    |                                                                                                                                                                                                      |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A rule never activates             | Check that it is enabled, and look at *Last evaluation* on the rule page: a value shown as unknown is older than *ignore values older than*. With *for at least*, the condition must hold that long. |
| A rule switches on and off rapidly | Add a *clear when* condition (hysteresis), a duration, or a cooldown ([ECA rules](#rules-eca--clear)).                                                                                               |
| A rule chain sends too many emails | Put a *Rate limit* or *Value changed* node before the email node.                                                                                                                                    |
| An action fails with "no access"   | The rule's owner needs at least the Operator role on the target device.                                                                                                                              |
| A scheduled run was skipped        | The execution log gives the reason: the condition was false or unknown, the rule was still active from the previous run, or the run could not start in time.                                         |
| No Telegram message                | Connect Telegram in My profile and use *Send test*. For groups and channels the bot must be a member or administrator.                                                                               |

<a id="troubleshooting--firmware"></a>

#### Firmware updates

|                                                               |                                                                                                   |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| No version to choose under *Update to*                        | No uploaded firmware for this device's board, or the board is not set.                            |
| The update fails after *Updating* and the old version is back | The new firmware did not start: check it over USB first. Use a partition scheme with OTA support. |
| Stays *Queued* and then fails                                 | The device was offline or does not run firmware that supports updates (the MQTT template does).   |

<a id="troubleshooting--account"></a>

#### Account

|                              |                                                                                           |
| ---------------------------- | ----------------------------------------------------------------------------------------- |
| No activation or reset email | Check the spam folder; request a new link on the login page. Links expire after 24 hours. |
| "Session expired" on a page  | Log in again; live updates resume.                                                        |

Still stuck? Contact the administrator of this platform.

<a id="administration"></a>

### For administrators

*Managing users and what administrators can see.*

This chapter is for users with the **Admin** role.

<a id="administration--users"></a>

#### Managing users

The *Users* menu (Administration) lists every account with its role, status and number of devices.

  - **Add user**: username, name, email, role and password. Use it for accounts that should not sign up themselves.
  - **Edit**: change the username, name, email, role, or make the account inactive (an inactive user cannot log in; their devices stay).
  - **Delete**: removes the user together with all their devices and data. This cannot be undone.
  - *Awaiting email confirmation* marks accounts that signed up but have not activated yet.

<a id="administration--see-all"></a>

#### What administrators see

  - All devices, locations, rules, alarms, firmware and activity of every user (lists show the owner).
  - Administrators may change and delete any device, and choose its owner.
  - Notes about the server's configuration that ordinary users do not see, for example when devices cannot reach an address shown on the device page.

<a id="administration--admin-site"></a>

#### Admin site

Staff accounts also see *Admin site* in the menu: a low-level editor for all stored data. Use it with care; changes there skip the checks of the normal pages. Apps that may use "Sign in with IoT Platform" ([Connected apps](#connected-apps)) are registered there.

<a id="administration--operations"></a>

#### Running the server

Installing, configuring and updating the server itself is described in the project's operator documentation, which is kept with the source code, not on this website.
