package dev.zcashflow.app

import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.net.wifi.WifiManager
import android.os.Build
import android.provider.Settings

/** Owns the permission and multicast lock; the Rust bridge never opens a socket. */
class LocalNetworkPermission(private val activity: MainActivity) {
  private val permission = "android.permission.ACCESS_LOCAL_NETWORK"
  private val requestCode = 47811
  private var multicastLock: WifiManager.MulticastLock? = null

  private fun required() = Build.VERSION.SDK_INT >= 37 && activity.applicationInfo.targetSdkVersion >= 37

  private fun granted() = !required() || activity.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED

  fun onCreate() {
    onResume()
    if (!granted()) activity.requestPermissions(arrayOf(permission), requestCode)
  }

  // Also handles a grant in Settings while the application is still running.
  fun onResume() {
    val allowed = granted()
    if (allowed && multicastLock == null) {
      val wifi = activity.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
      multicastLock = wifi.createMulticastLock("zcashflow-sync").apply {
        setReferenceCounted(true)
        acquire()
      }
    } else if (!allowed) {
      onDestroy()
    }
    // Publish only after the multicast lock is held, so mDNS can start immediately.
    activity.setLocalNetworkPermission(allowed)
  }

  fun onRequestPermissionsResult(code: Int) {
    if (code != requestCode) return
    onResume()
    if (!granted()) {
      AlertDialog.Builder(activity)
        .setTitle("Local network access")
        .setMessage("Device sync needs local network access. You can keep using ZCashflow offline, or allow access in Android Settings → Permissions → Nearby devices.")
        .setNegativeButton(android.R.string.cancel, null)
        .setPositiveButton("Settings") { _, _ ->
          activity.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${activity.packageName}")))
        }
        .show()
    }
  }

  fun onDestroy() {
    multicastLock?.let { if (it.isHeld) it.release() }
    multicastLock = null
  }
}
