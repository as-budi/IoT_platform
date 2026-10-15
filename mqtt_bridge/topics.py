"""
Nama-nama topik MQTT yang dipakai platform.

Semua topik diawali MQTT_TOPIC_PREFIX (dari .env), lalu access token device:

    <prefix>/<token>/telemetry                 device -> platform  data sensor
    <prefix>/<token>/desired                   platform -> device  desired state (retained)
    <prefix>/<token>/commands/<id>             platform -> device  command
    <prefix>/<token>/commands/<id>/response    device -> platform  hasil command
    <prefix>/<token>/errors                    platform -> device  pesan kesalahan
    <prefix>/<token>/firmware                  device -> platform  versi firmware & progres OTA (tahap 9)

Mengapa ada prefix? Broker publik dipakai bersama oleh siapa saja di internet.
Prefix yang unik mencegah topik kita bertabrakan dengan topik orang lain.

File ini sengaja tidak mengimpor Django maupun paho-mqtt: isinya hanya
pengolahan teks, sehingga mudah dites dan dipakai di mana saja.
"""
TELEMETRY = "telemetry"
COMMAND_RESPONSE = "command_response"
FIRMWARE = "firmware"


def device_base(prefix, token):
    return f"{prefix}/{token}"


def telemetry(prefix, token):
    return f"{device_base(prefix, token)}/telemetry"


def desired(prefix, token):
    return f"{device_base(prefix, token)}/desired"


def command(prefix, token, command_id):
    return f"{device_base(prefix, token)}/commands/{command_id}"


def command_response(prefix, token, command_id):
    return f"{command(prefix, token, command_id)}/response"


def errors(prefix, token):
    return f"{device_base(prefix, token)}/errors"


def firmware(prefix, token):
    return f"{device_base(prefix, token)}/firmware"


def subscriptions(prefix):
    """Topik yang didengarkan platform. `+` = wildcard tepat satu level topik."""
    return [
        f"{prefix}/+/telemetry",
        f"{prefix}/+/commands/+/response",
        f"{prefix}/+/firmware",
    ]


def parse(topic, prefix):
    """Pecah topik masuk menjadi (jenis, token, command_id).

        parse("demo/AbC/telemetry", "demo")             -> ("telemetry", "AbC", None)
        parse("demo/AbC/commands/12/response", "demo")  -> ("command_response", "AbC", 12)
        parse("demo/AbC/firmware", "demo")              -> ("firmware", "AbC", None)

    Topik lain (bukan milik kita, atau bentuknya salah) -> None.
    """
    start = prefix + "/"
    if not topic.startswith(start):
        return None
    parts = topic[len(start):].split("/")
    if len(parts) == 2 and parts[0] and parts[1] == "telemetry":
        return (TELEMETRY, parts[0], None)
    if len(parts) == 2 and parts[0] and parts[1] == "firmware":
        return (FIRMWARE, parts[0], None)
    if (
        len(parts) == 4
        and parts[0]
        and parts[1] == "commands"
        and parts[2].isdigit()
        and parts[3] == "response"
    ):
        return (COMMAND_RESPONSE, parts[0], int(parts[2]))
    return None
