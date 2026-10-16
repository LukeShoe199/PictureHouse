package com.picturehouse.android

import android.annotation.SuppressLint
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.ActivityInfo
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.window.OnBackInvokedDispatcher
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/**
 * PictureHouse for Android.
 *
 * Finds the server on the Wi-Fi (or takes an address), then shows the
 * PictureHouse console full screen. The console calls back through the
 * `PictureHouseAndroid` JavaScript interface to hand videos to other apps
 * (VLC and friends, which can also cast to a Chromecast) and to keep the
 * screen on during playback.
 */
class MainActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private val prefs by lazy { getSharedPreferences("picturehouse", MODE_PRIVATE) }

    private lateinit var root: FrameLayout
    private lateinit var web: WebView
    private lateinit var connect: ScrollView
    private lateinit var status: TextView
    private lateinit var spinner: ProgressBar
    private lateinit var foundList: LinearLayout
    private lateinit var address: EditText

    private var serverBase: String? = null
    private var fullscreenView: View? = null
    private var fullscreenCallback: WebChromeClient.CustomViewCallback? = null

    // ------------------------------------------------------------------ lifecycle

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        root = FrameLayout(this).apply { setBackgroundColor(color(R.color.bg)) }
        web = buildWebView()
        connect = buildConnectScreen()
        root.addView(web, FrameLayout.LayoutParams(MATCH, MATCH))
        root.addView(connect, FrameLayout.LayoutParams(MATCH, MATCH))
        setContentView(root)
        applyInsets()
        // Android 13+: back gestures go to this callback (on Android 16 with
        // predictive back, onBackPressed below is never called for them).
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            onBackInvokedDispatcher.registerOnBackInvokedCallback(
                OnBackInvokedDispatcher.PRIORITY_DEFAULT,
            ) { if (!handleBack()) finish() }
        }

        if (savedInstanceState != null && web.restoreState(savedInstanceState) != null) {
            serverBase = prefs.getString(KEY_SERVER, null)
            showWeb()
            return
        }
        val saved = prefs.getString(KEY_SERVER, null)
        if (saved != null) tryServer(saved, fromSaved = true) else startSearch()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    override fun onPause() {
        super.onPause()
        web.onPause()
    }

    override fun onResume() {
        super.onResume()
        web.onResume()
    }

    override fun onDestroy() {
        web.destroy()
        super.onDestroy()
    }

    /** Back: leave full screen, then go back in the console, then leave the app. */
    private fun handleBack(): Boolean = when {
        fullscreenView != null -> { exitFullscreen(); true }
        web.visibility == View.VISIBLE && web.canGoBack() -> { web.goBack(); true }
        else -> false
    }

    // Gestures on Android 13+ are handled by the OnBackInvokedCallback in onCreate.
    @SuppressLint("GestureBackNavigation")
    @Deprecated("Back button/gesture before Android 13 (and 13-15 without predictive back)")
    override fun onBackPressed() {
        if (!handleBack()) @Suppress("DEPRECATION") super.onBackPressed()
    }

    // ------------------------------------------------------------------ web view

    @SuppressLint("SetJavaScriptEnabled")
    private fun buildWebView(): WebView = WebView(this).apply {
        visibility = View.GONE
        setBackgroundColor(color(R.color.bg))
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.mediaPlaybackRequiresUserGesture = false
        settings.userAgentString = settings.userAgentString + " PictureHouseAndroid/1.0"
        addJavascriptInterface(Bridge(), "PictureHouseAndroid")

        webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url
                val base = serverBase?.let { Uri.parse(it) }
                // Stay inside the app for the server's own pages; open anything
                // else (TMDb links, help pages, the CA certificate) outside.
                if (base != null && url.host == base.host && url.port == base.port) {
                    val path = url.path.orEmpty()
                    if (!path.endsWith(".crt") && !path.endsWith(".apk")) return false
                }
                openExternal(url.toString(), null)
                return true
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) {
                    showConnect("Couldn't reach ${serverBase ?: "the server"} (${error.description}).\n" +
                        "Is the PC on and PictureHouse running?")
                    startSearch(keepMessage = true)
                }
            }
        }

        webChromeClient = object : WebChromeClient() {
            // The console's <video> going full screen.
            override fun onShowCustomView(view: View, callback: CustomViewCallback) {
                if (fullscreenView != null) {
                    callback.onCustomViewHidden()
                    return
                }
                fullscreenView = view
                fullscreenCallback = callback
                root.addView(view, FrameLayout.LayoutParams(MATCH, MATCH))
                setSystemBarsHidden(true)
                // Films are wide: turn sideways (either way round) while full screen.
                requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
            }

            override fun onHideCustomView() = exitFullscreen()
        }

        setDownloadListener { url, _, _, mime, _ -> openExternal(url, mime) }
    }

    private fun exitFullscreen() {
        fullscreenView?.let { root.removeView(it) }
        fullscreenView = null
        fullscreenCallback?.onCustomViewHidden()
        fullscreenCallback = null
        setSystemBarsHidden(false)
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
    }

    /** Immersive full screen: no status bar or navigation bar (a swipe from
     *  the edge shows them briefly). Android 11+ API, else the older flags:
     *  the app supports Android 8, where insetsController doesn't exist. */
    private fun setSystemBarsHidden(on: Boolean) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val c = window.insetsController ?: return
            if (on) {
                c.hide(WindowInsets.Type.systemBars())
                c.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            } else {
                c.show(WindowInsets.Type.systemBars())
            }
        } else {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = if (on) {
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or View.SYSTEM_UI_FLAG_FULLSCREEN or
                    View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_LAYOUT_STABLE or
                    View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
            } else {
                0
            }
        }
    }

    /** Keep the console clear of the status and navigation bars. */
    private fun applyInsets() {
        root.setOnApplyWindowInsetsListener { v, insets ->
            if (fullscreenView == null) {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.ime())
                    v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
                } else {
                    @Suppress("DEPRECATION")
                    v.setPadding(insets.systemWindowInsetLeft, insets.systemWindowInsetTop,
                        insets.systemWindowInsetRight, insets.systemWindowInsetBottom)
                }
            } else {
                v.setPadding(0, 0, 0, 0)
            }
            insets
        }
    }

    private fun showWeb() {
        connect.visibility = View.GONE
        web.visibility = View.VISIBLE
    }

    private fun loadServer(base: String) {
        serverBase = base
        prefs.edit().putString(KEY_SERVER, base).apply()
        showWeb()
        web.loadUrl(base)
    }

    /** Handed to the console as window.PictureHouseAndroid. */
    inner class Bridge {
        /** Open a media URL in another app (VLC, MX Player, BubbleUPnP...). */
        @JavascriptInterface
        fun openExternal(url: String, mime: String?, title: String?) {
            main.post { this@MainActivity.openExternal(url, mime, title) }
        }

        /** Same, starting at [positionMs] (VLC and MX Player read the "position" extra). */
        @JavascriptInterface
        fun openExternalAt(url: String, mime: String?, title: String?, positionMs: Long) {
            main.post { this@MainActivity.openExternal(url, mime, title, positionMs) }
        }

        @JavascriptInterface
        fun keepScreenOn(on: Boolean) {
            main.post {
                if (on) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            }
        }

        @JavascriptInterface
        fun changeServer() {
            main.post {
                prefs.edit().remove(KEY_SERVER).apply()
                showConnect("Looking for PictureHouse on your Wi-Fiâ€¦")
                startSearch(keepMessage = true)
            }
        }

        @JavascriptInterface
        // The real installed version (set from PictureHouse's version at build time).
        fun version(): String =
            runCatching { packageManager.getPackageInfo(packageName, 0).versionName }.getOrNull() ?: "0.0.0"
    }

    private fun openExternal(url: String, mime: String?, title: String? = null, positionMs: Long = 0) {
        val uri = Uri.parse(url)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            if (mime.isNullOrBlank()) data = uri else setDataAndType(uri, mime)
            if (!title.isNullOrBlank()) putExtra("title", title) // VLC shows this
            if (positionMs > 0) putExtra("position", positionMs)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        try {
            val media = mime != null && (mime.startsWith("video/") || mime.startsWith("audio/") ||
                mime.contains("mpegurl"))
            val chooser = if (media)
                Intent.createChooser(intent, "Play with") else intent
            startActivity(chooser)
        } catch (_: ActivityNotFoundException) {
            Toast.makeText(this, "No app on this phone can open that. Try installing VLC.", Toast.LENGTH_LONG).show()
        }
    }

    // ------------------------------------------------------------------ connect screen

    private fun buildConnectScreen(): ScrollView {
        val pad = dp(24)
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(pad, dp(48), pad, pad)
        }
        val logo = TextView(this).apply {
            text = "PH"
            gravity = Gravity.CENTER
            setTextColor(color(R.color.ink))
            setTypeface(typeface, Typeface.BOLD)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 26f)
            background = GradientDrawable().apply {
                cornerRadius = dp(18).toFloat()
                setColor(color(R.color.accent))
            }
        }
        column.addView(logo, LinearLayout.LayoutParams(dp(72), dp(72)).apply { bottomMargin = dp(16) })
        column.addView(label("PictureHouse", 24f, R.color.fg, bold = true))
        status = label("Looking for PictureHouse on your Wi-Fiâ€¦", 15f, R.color.muted).apply {
            gravity = Gravity.CENTER
            setPadding(0, dp(8), 0, dp(16))
        }
        column.addView(status)
        spinner = ProgressBar(this)
        column.addView(spinner)
        foundList = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        column.addView(foundList, LinearLayout.LayoutParams(MATCH, WRAP).apply { topMargin = dp(12) })

        column.addView(button("Search again", primary = false) { startSearch() },
            LinearLayout.LayoutParams(MATCH, WRAP).apply { topMargin = dp(8) })

        column.addView(label("Or enter the PC's address", 13f, R.color.muted).apply {
            setPadding(0, dp(28), 0, dp(6))
        }, LinearLayout.LayoutParams(MATCH, WRAP))
        address = EditText(this).apply {
            hint = "192.168.1.20:9595"
            setHintTextColor(color(R.color.muted))
            setTextColor(color(R.color.fg))
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            imeOptions = EditorInfo.IME_ACTION_GO
            setSingleLine()
            setText(prefs.getString(KEY_SERVER, "")?.removePrefix("http://")?.trimEnd('/'))
            setOnEditorActionListener { _, id, e ->
                if (id == EditorInfo.IME_ACTION_GO || e?.keyCode == KeyEvent.KEYCODE_ENTER) {
                    connectManually(); true
                } else false
            }
        }
        column.addView(address, LinearLayout.LayoutParams(MATCH, WRAP))
        column.addView(button("Connect", primary = true) { connectManually() },
            LinearLayout.LayoutParams(MATCH, WRAP).apply { topMargin = dp(8) })
        column.addView(label("The address is shown on the PC in the PictureHouse window " +
            "(Library â†’ DLNA), e.g. 192.168.1.20:9595.", 12.5f, R.color.muted).apply {
            setPadding(0, dp(10), 0, 0)
        })
        return ScrollView(this).apply {
            isFillViewport = true
            setBackgroundColor(color(R.color.bg))
            addView(column, FrameLayout.LayoutParams(MATCH, WRAP))
        }
    }

    private fun showConnect(message: String) {
        exitFullscreen()
        web.visibility = View.GONE
        connect.visibility = View.VISIBLE
        status.text = message
    }

    private fun startSearch(keepMessage: Boolean = false) {
        connect.visibility = View.VISIBLE
        web.visibility = View.GONE
        if (!keepMessage) status.text = "Looking for PictureHouse on your Wi-Fiâ€¦"
        spinner.visibility = View.VISIBLE
        foundList.removeAllViews()
        Thread {
            val found = Discovery.search(this)
            main.post { showFound(found, keepMessage) }
        }.start()
    }

    private fun showFound(found: List<Found>, keepMessage: Boolean) {
        spinner.visibility = View.GONE
        foundList.removeAllViews()
        when {
            found.size == 1 && !keepMessage -> loadServer(found[0].baseUrl) // the usual case: go straight in
            found.isEmpty() -> if (!keepMessage) status.text =
                "No PictureHouse server found on this Wi-Fi.\nCheck the PC is on and PictureHouse is " +
                    "running, or enter its address below."
            else -> {
                if (!keepMessage) status.text = "Choose a server:"
                for (f in found) {
                    foundList.addView(button("${f.name}\n${f.baseUrl.removePrefix("http://").trimEnd('/')}",
                        primary = true) { loadServer(f.baseUrl) },
                        LinearLayout.LayoutParams(MATCH, WRAP).apply { topMargin = dp(8) })
                }
            }
        }
    }

    private fun connectManually() {
        val base = Discovery.normalise(address.text.toString())
        if (base == null) {
            status.text = "That doesn't look like an address. Try something like 192.168.1.20:9595."
            return
        }
        tryServer(base, fromSaved = false)
    }

    private fun tryServer(base: String, fromSaved: Boolean) {
        status.text = "Connecting to ${base.removePrefix("http://").trimEnd('/')}â€¦"
        spinner.visibility = View.VISIBLE
        Thread {
            val name = Discovery.status(base)
            main.post {
                spinner.visibility = View.GONE
                when {
                    name != null -> loadServer(base)
                    fromSaved -> startSearch() // the PC's address may have changed
                    else -> status.text = "No PictureHouse server answered at " +
                        "${base.removePrefix("http://").trimEnd('/')}."
                }
            }
        }.start()
    }

    // ------------------------------------------------------------------ helpers

    private fun label(text: String, sp: Float, colorRes: Int, bold: Boolean = false) = TextView(this).apply {
        this.text = text
        setTextColor(color(colorRes))
        setTextSize(TypedValue.COMPLEX_UNIT_SP, sp)
        if (bold) setTypeface(typeface, Typeface.BOLD)
    }

    private fun button(text: String, primary: Boolean, onClick: () -> Unit) = Button(this).apply {
        this.text = text
        isAllCaps = false
        setTextColor(color(if (primary) R.color.ink else R.color.fg))
        background = GradientDrawable().apply {
            cornerRadius = dp(10).toFloat()
            setColor(color(if (primary) R.color.accent else R.color.surface))
        }
        setPadding(dp(16), dp(12), dp(16), dp(12))
        setOnClickListener { onClick() }
    }

    private fun color(res: Int) = getColor(res)
    private fun dp(v: Int) = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

    companion object {
        private const val KEY_SERVER = "server"
        private const val MATCH = ViewGroup.LayoutParams.MATCH_PARENT
        private const val WRAP = ViewGroup.LayoutParams.WRAP_CONTENT
    }
}
