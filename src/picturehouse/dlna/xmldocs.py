"""Static UPnP documents: device description and service descriptions (SCPD)."""

from __future__ import annotations

from xml.sax.saxutils import escape

from picturehouse import __version__

CDS_TYPE = "urn:schemas-upnp-org:service:ContentDirectory:1"
CMS_TYPE = "urn:schemas-upnp-org:service:ConnectionManager:1"


def device_description(friendly_name: str, uuid: str, base_url: str) -> str:
    name = escape(friendly_name)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:dlna="urn:schemas-dlna-org:device-1-0" xmlns:sec="http://www.sec.co.kr/dlna">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <dlna:X_DLNADOC>DMS-1.50</dlna:X_DLNADOC>
    <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
    <friendlyName>{name}</friendlyName>
    <manufacturer>PictureHouse</manufacturer>
    <manufacturerURL>{escape(base_url)}/</manufacturerURL>
    <modelDescription>Home media server</modelDescription>
    <modelName>PictureHouse</modelName>
    <modelNumber>{__version__}</modelNumber>
    <modelURL>{escape(base_url)}/</modelURL>
    <serialNumber>{uuid[:8]}</serialNumber>
    <UDN>uuid:{uuid}</UDN>
    <sec:ProductCap>smi,DCM10,getMediaInfo.sec,getCaptionInfo.sec</sec:ProductCap>
    <sec:X_ProductCap>smi,DCM10,getMediaInfo.sec,getCaptionInfo.sec</sec:X_ProductCap>
    <iconList>
      <icon><mimetype>image/png</mimetype><width>120</width><height>120</height><depth>24</depth><url>/dlna/icon-120.png</url></icon>
      <icon><mimetype>image/png</mimetype><width>48</width><height>48</height><depth>24</depth><url>/dlna/icon-48.png</url></icon>
    </iconList>
    <serviceList>
      <service>
        <serviceType>{CDS_TYPE}</serviceType>
        <serviceId>urn:upnp-org:serviceId:ContentDirectory</serviceId>
        <SCPDURL>/dlna/cds.xml</SCPDURL>
        <controlURL>/dlna/cds/control</controlURL>
        <eventSubURL>/dlna/cds/event</eventSubURL>
      </service>
      <service>
        <serviceType>{CMS_TYPE}</serviceType>
        <serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>
        <SCPDURL>/dlna/cms.xml</SCPDURL>
        <controlURL>/dlna/cms/control</controlURL>
        <eventSubURL>/dlna/cms/event</eventSubURL>
      </service>
    </serviceList>
    <presentationURL>{escape(base_url)}/</presentationURL>
  </device>
</root>
"""


def _arg(name: str, direction: str, var: str) -> str:
    return (f"<argument><name>{name}</name><direction>{direction}</direction>"
            f"<relatedStateVariable>{var}</relatedStateVariable></argument>")  # fmt: skip


def _action(name: str, *args: str) -> str:
    return f"<action><name>{name}</name><argumentList>{''.join(args)}</argumentList></action>"


def _var(name: str, dtype: str, events: bool = False, allowed: list[str] | None = None) -> str:
    vals = ""
    if allowed:
        vals = "<allowedValueList>" + "".join(
            f"<allowedValue>{v}</allowedValue>" for v in allowed) + "</allowedValueList>"  # fmt: skip
    ev = "yes" if events else "no"
    return (f'<stateVariable sendEvents="{ev}"><name>{name}</name>'
            f"<dataType>{dtype}</dataType>{vals}</stateVariable>")  # fmt: skip


def _scpd(actions: list[str], variables: list[str]) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<scpd xmlns="urn:schemas-upnp-org:service-1-0">'
            "<specVersion><major>1</major><minor>0</minor></specVersion>"
            f"<actionList>{''.join(actions)}</actionList>"
            f"<serviceStateTable>{''.join(variables)}</serviceStateTable></scpd>")  # fmt: skip


CDS_SCPD = _scpd(
    [
        _action("Browse",
                _arg("ObjectID", "in", "A_ARG_TYPE_ObjectID"),
                _arg("BrowseFlag", "in", "A_ARG_TYPE_BrowseFlag"),
                _arg("Filter", "in", "A_ARG_TYPE_Filter"),
                _arg("StartingIndex", "in", "A_ARG_TYPE_Index"),
                _arg("RequestedCount", "in", "A_ARG_TYPE_Count"),
                _arg("SortCriteria", "in", "A_ARG_TYPE_SortCriteria"),
                _arg("Result", "out", "A_ARG_TYPE_Result"),
                _arg("NumberReturned", "out", "A_ARG_TYPE_Count"),
                _arg("TotalMatches", "out", "A_ARG_TYPE_Count"),
                _arg("UpdateID", "out", "A_ARG_TYPE_UpdateID")),
        _action("GetSearchCapabilities", _arg("SearchCaps", "out", "SearchCapabilities")),
        _action("GetSortCapabilities", _arg("SortCaps", "out", "SortCapabilities")),
        _action("GetSystemUpdateID", _arg("Id", "out", "SystemUpdateID")),
    ],
    [
        _var("A_ARG_TYPE_ObjectID", "string"),
        _var("A_ARG_TYPE_BrowseFlag", "string", allowed=["BrowseMetadata", "BrowseDirectChildren"]),
        _var("A_ARG_TYPE_Filter", "string"),
        _var("A_ARG_TYPE_Index", "ui4"),
        _var("A_ARG_TYPE_Count", "ui4"),
        _var("A_ARG_TYPE_SortCriteria", "string"),
        _var("A_ARG_TYPE_Result", "string"),
        _var("A_ARG_TYPE_UpdateID", "ui4"),
        _var("SearchCapabilities", "string"),
        _var("SortCapabilities", "string"),
        _var("SystemUpdateID", "ui4", events=True),
    ],
)  # fmt: skip

CMS_SCPD = _scpd(
    [
        _action("GetProtocolInfo",
                _arg("Source", "out", "SourceProtocolInfo"),
                _arg("Sink", "out", "SinkProtocolInfo")),
        _action("GetCurrentConnectionIDs",
                _arg("ConnectionIDs", "out", "CurrentConnectionIDs")),
        _action("GetCurrentConnectionInfo",
                _arg("ConnectionID", "in", "A_ARG_TYPE_ConnectionID"),
                _arg("RcsID", "out", "A_ARG_TYPE_RcsID"),
                _arg("AVTransportID", "out", "A_ARG_TYPE_AVTransportID"),
                _arg("ProtocolInfo", "out", "A_ARG_TYPE_ProtocolInfo"),
                _arg("PeerConnectionManager", "out", "A_ARG_TYPE_ConnectionManager"),
                _arg("PeerConnectionID", "out", "A_ARG_TYPE_ConnectionID"),
                _arg("Direction", "out", "A_ARG_TYPE_Direction"),
                _arg("Status", "out", "A_ARG_TYPE_ConnectionStatus")),
    ],
    [
        _var("SourceProtocolInfo", "string", events=True),
        _var("SinkProtocolInfo", "string", events=True),
        _var("CurrentConnectionIDs", "string", events=True),
        _var("A_ARG_TYPE_ConnectionStatus", "string",
             allowed=["OK", "ContentFormatMismatch", "InsufficientBandwidth",
                      "UnreliableChannel", "Unknown"]),
        _var("A_ARG_TYPE_ConnectionManager", "string"),
        _var("A_ARG_TYPE_Direction", "string", allowed=["Input", "Output"]),
        _var("A_ARG_TYPE_ProtocolInfo", "string"),
        _var("A_ARG_TYPE_ConnectionID", "i4"),
        _var("A_ARG_TYPE_AVTransportID", "i4"),
        _var("A_ARG_TYPE_RcsID", "i4"),
    ],
)  # fmt: skip


def soap_response(service: str, action: str, values: dict[str, str | int]) -> str:
    body = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in values.items())
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action}Response xmlns:u="{service}">{body}</u:{action}Response>'
            "</s:Body></s:Envelope>")  # fmt: skip


def soap_fault(code: int, description: str) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><s:Fault>'
            "<faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring><detail>"
            '<UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
            f"<errorCode>{code}</errorCode><errorDescription>{escape(description)}"
            "</errorDescription></UPnPError></detail></s:Fault></s:Body></s:Envelope>")  # fmt: skip


def event_propertyset(values: dict[str, str | int]) -> str:
    props = "".join(f"<e:property><{k}>{escape(str(v))}</{k}></e:property>"
                    for k, v in values.items())  # fmt: skip
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">{props}</e:propertyset>'
    )


def icon_png(size: int, rgba: bool = False) -> bytes:
    """The rail logo as a PNG: amber rounded square with a dark play triangle.

    ``rgba``: transparent outside the rounded square (desktop/tray icons);
    otherwise RGB on the app's dark background (TVs, PWA).
    """
    import struct
    import zlib

    amber, ink, bg = (0xF2, 0xB3, 0x3D), (0x1B, 0x13, 0x05), (0x0A, 0x0D, 0x11)
    r = size * 0.22
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            cx, cy = x + 0.5, y + 0.5
            # rounded-square mask
            dx = max(r - cx, 0, cx - (size - r))
            dy = max(r - cy, 0, cy - (size - r))
            inside = dx * dx + dy * dy <= r * r
            col = (*bg, 0) if rgba else bg
            if inside:
                col = amber
                # play triangle, centred
                tx, ty = (cx - size * 0.38) / (size * 0.32), (cy - size * 0.5) / (size * 0.34)
                if 0 <= tx <= 1 and abs(ty) <= 1 - tx:
                    col = ink
                if rgba:
                    col = (*col, 255)
            row += bytes(col)
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(
            ">I", zlib.crc32(tag + data) & 0xFFFFFFFF)  # fmt: skip

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6 if rgba else 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")
