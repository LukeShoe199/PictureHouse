package com.picturehouse.android

import android.content.Context
import android.net.wifi.WifiManager
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.MulticastSocket
import java.net.SocketTimeoutException
import java.net.URL

/** A PictureHouse server found on the network. */
data class Found(val name: String, val baseUrl: String)

/**
 * Finds PictureHouse servers on the Wi-Fi with the same SSDP search a TV uses,
 * then confirms each candidate by asking its /api/status.
 */
object Discovery {
    private const val SSDP_ADDR = "239.255.255.250"
    private const val SSDP_PORT = 1900
    private const val SEARCH =
        "M-SEARCH * HTTP/1.1\r\n" +
            "HOST: 239.255.255.250:1900\r\n" +
            "MAN: \"ssdp:discover\"\r\n" +
            "MX: 2\r\n" +
            "ST: urn:schemas-upnp-org:device:MediaServer:1\r\n" +
            "USER-AGENT: Android PictureHouse/1.0 UPnP/1.0\r\n\r\n"

    /** Blocking; call from a background thread. */
    fun search(context: Context, timeoutMs: Int = 3500): List<Found> {
        val wifi = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        // Android drops multicast replies unless an app holds this lock.
        val lock = wifi.createMulticastLock("picturehouse-ssdp").apply {
            setReferenceCounted(false)
            acquire()
        }
        val bases = linkedSetOf<String>()
        try {
            MulticastSocket(null).use { sock ->
                sock.reuseAddress = true
                sock.bind(null)
                sock.soTimeout = 400
                val group = InetAddress.getByName(SSDP_ADDR)
                val data = SEARCH.toByteArray()
                val deadline = System.currentTimeMillis() + timeoutMs
                var sends = 0
                val buf = ByteArray(4096)
                while (System.currentTimeMillis() < deadline) {
                    if (sends < 3) { // UDP is lossy: ask a few times
                        sock.send(DatagramPacket(data, data.size, group, SSDP_PORT))
                        sends++
                    }
                    try {
                        val pkt = DatagramPacket(buf, buf.size)
                        sock.receive(pkt)
                        val text = String(pkt.data, 0, pkt.length)
                        val headers = text.split("\r\n").mapNotNull { line ->
                            val i = line.indexOf(':')
                            if (i > 0) line.substring(0, i).trim().uppercase() to line.substring(i + 1).trim() else null
                        }.toMap()
                        val server = headers["SERVER"].orEmpty()
                        val location = headers["LOCATION"] ?: continue
                        // Other media servers (Serviio, Plex...) answer too; keep ours.
                        if (!server.contains("picturehouse", ignoreCase = true)) continue
                        val u = URL(location)
                        bases.add("${u.protocol}://${u.host}:${u.port}/")
                    } catch (_: SocketTimeoutException) {
                    }
                }
            }
        } catch (_: Exception) {
            // No Wi-Fi or multicast blocked: fall through with whatever was found.
        } finally {
            lock.release()
        }
        return bases.mapNotNull { base -> status(base)?.let { Found(it, base) } }
    }

    /** The server's display name if a PictureHouse server answers at [base], else null. */
    fun status(base: String, timeoutMs: Int = 2500): String? = try {
        val conn = URL(base.trimEnd('/') + "/api/status").openConnection() as HttpURLConnection
        conn.connectTimeout = timeoutMs
        conn.readTimeout = timeoutMs
        conn.inputStream.bufferedReader().use { r ->
            JSONObject(r.readText()).getJSONObject("server").optString("name", "PictureHouse")
        }.also { conn.disconnect() }
    } catch (_: Exception) {
        null
    }

    /** Accepts "192.168.1.20", "192.168.1.20:9595" or a full URL. */
    fun normalise(input: String): String? {
        var s = input.trim()
        if (s.isEmpty()) return null
        if (!s.startsWith("http://") && !s.startsWith("https://")) s = "http://$s"
        return try {
            val u = URL(s)
            val port = if (u.port == -1) (if (u.protocol == "https") 9596 else 9595) else u.port
            "${u.protocol}://${u.host}:$port/"
        } catch (_: Exception) {
            null
        }
    }
}
