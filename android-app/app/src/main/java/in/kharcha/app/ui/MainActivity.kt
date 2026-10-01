package `in`.kharcha.app.ui

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import dagger.hilt.android.AndroidEntryPoint
import `in`.kharcha.app.ui.cash.CashScreen
import `in`.kharcha.app.ui.events.EventsScreen
import `in`.kharcha.app.ui.home.HomeScreen
import `in`.kharcha.app.ui.settings.SettingsScreen
import `in`.kharcha.app.ui.transactions.TransactionsScreen

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { KharchaTheme { KharchaNav() } }
    }
}

private enum class Tab(val route: String, val label: String, val icon: ImageVector) {
    HOME("home", "Home", Icons.Filled.Home),
    EVENTS("events", "Captured", Icons.AutoMirrored.Filled.List),
    TRANSACTIONS("transactions", "Payments", Icons.Filled.Menu),
    CASH("cash", "Cash", Icons.Filled.Add),
    SETTINGS("settings", "Settings", Icons.Filled.Settings),
}

@Composable
private fun KharchaNav() {
    val nav = rememberNavController()
    val backStack by nav.currentBackStackEntryAsState()
    Scaffold(
        bottomBar = {
            NavigationBar {
                Tab.entries.forEach { tab ->
                    NavigationBarItem(
                        selected = backStack?.destination?.route == tab.route,
                        onClick = {
                            nav.navigate(tab.route) {
                                popUpTo(nav.graph.findStartDestination().id) { saveState = true }
                                launchSingleTop = true
                                restoreState = true
                            }
                        },
                        icon = { Icon(tab.icon, contentDescription = tab.label) },
                        label = { Text(tab.label) },
                    )
                }
            }
        },
    ) { padding ->
        NavHost(nav, startDestination = Tab.HOME.route, modifier = Modifier.padding(padding)) {
            composable(Tab.HOME.route) { HomeScreen() }
            composable(Tab.EVENTS.route) { EventsScreen(onOpenSettings = { nav.navigate(Tab.SETTINGS.route) }) }
            composable(Tab.TRANSACTIONS.route) { TransactionsScreen() }
            composable(Tab.CASH.route) { CashScreen() }
            composable(Tab.SETTINGS.route) { SettingsScreen() }
        }
    }
}
