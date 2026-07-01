import 'dart:convert';
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:local_notifier/local_notifier.dart';
import 'package:window_manager/window_manager.dart';
import 'package:tray_manager/tray_manager.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await localNotifier.setup(
    appName: 'Attendance Notifier',
    shortcutPolicy: ShortcutPolicy.requireCreate,
  );
  
  await windowManager.ensureInitialized();
  WindowOptions windowOptions = const WindowOptions(
    size: Size(800, 600),
    center: true,
    title: 'Attendance Notifier',
  );
  windowManager.waitUntilReadyToShow(windowOptions, () async {
    await windowManager.show();
    await windowManager.focus();
    await windowManager.setPreventClose(true);
  });

  runApp(const AttendanceApp());
}

class AttendanceApp extends StatelessWidget {
  const AttendanceApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Attendance Notifier',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.blue),
        useMaterial3: true,
      ),
      home: const DashboardScreen(),
    );
  }
}

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key});

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> with TrayListener, WindowListener {
  final TextEditingController _urlController = TextEditingController(text: 'ws://192.168.0.176/ws/attendance/');
  WebSocketChannel? _channel;
  bool _isConnected = false;
  final List<Map<String, dynamic>> _punches = [];
  
  bool _showOverlay = false;
  Map<String, String>? _currentPunchData;
  Timer? _overlayTimer;

  @override
  void initState() {
    super.initState();
    trayManager.addListener(this);
    windowManager.addListener(this);
    _initTray();
  }

  @override
  void onWindowClose() async {
    await windowManager.hide();
  }

  void _restoreWindow() async {
    await windowManager.show();
    await windowManager.focus();
  }

  void _initTray() async {
    await trayManager.setIcon('assets/tray_icon_red.png');
    Menu menu = Menu(
      items: [
        MenuItem(
          key: 'show_window',
          label: 'Show Dashboard',
        ),
        MenuItem.separator(),
        MenuItem(
          key: 'exit_app',
          label: 'Exit',
        ),
      ],
    );
    await trayManager.setContextMenu(menu);
  }

  @override
  void onTrayIconMouseDown() {
    _restoreWindow();
  }

  @override
  void onTrayMenuItemClick(MenuItem menuItem) async {
    if (menuItem.key == 'show_window') {
      _restoreWindow();
    } else if (menuItem.key == 'exit_app') {
      await trayManager.destroy();
      await windowManager.destroy();
    }
  }

  void _updateTrayIcon() {
    if (_isConnected) {
      trayManager.setIcon('assets/tray_icon.png');
    } else {
      trayManager.setIcon('assets/tray_icon_red.png');
    }
  }

  void _connect() {
    if (_urlController.text.isEmpty) return;

    setState(() {
      _isConnected = true;
      _channel = WebSocketChannel.connect(Uri.parse(_urlController.text));
    });
    _updateTrayIcon();

    _channel!.stream.listen(
      (message) {
        try {
          final data = jsonDecode(message);
          // Assuming the message contains a 'data' field based on standard django channels setup
          final payload = data['data'] ?? data;
          final name = payload['name'] ?? 'Unknown';
          final empId = payload['employee_id'] ?? 'Unknown ID';
          final time = payload['time'] ?? 'Unknown Time';

          _showNotification(name, empId, time);
          _showFullScreenPunch(name, empId, time);

          setState(() {
            _punches.insert(0, {
              'name': name,
              'employee_id': empId,
              'time': time,
            });
            // Keep only latest 50
            if (_punches.length > 50) {
              _punches.removeLast();
            }
          });
        } catch (e) {
          debugPrint('Error parsing message: $e');
        }
      },
      onDone: () {
        setState(() {
          _isConnected = false;
        });
        _updateTrayIcon();
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Disconnected from server')),
        );
      },
      onError: (error) {
        setState(() {
          _isConnected = false;
        });
        _updateTrayIcon();
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('WebSocket Error: $error')),
        );
      },
    );
  }

  void _disconnect() {
    _channel?.sink.close();
    setState(() {
      _isConnected = false;
    });
    _updateTrayIcon();
  }

  void _showNotification(String name, String empId, String time) {
    LocalNotification notification = LocalNotification(
      title: "New Punch: $name",
      body: "Employee ID: $empId\nTime: $time",
    );
    notification.onShow = () {
      debugPrint('onShow ${notification.identifier}');
    };
    notification.onClose = (closeReason) {
      debugPrint('onClose ${_enumToString(closeReason)}');
    };
    notification.onClick = () {
      debugPrint('onClick ${notification.identifier}');
    };
    notification.show();
  }

  void _showFullScreenPunch(String name, String empId, String time) async {
    setState(() {
      _currentPunchData = {'name': name, 'empId': empId, 'time': time};
      _showOverlay = true;
    });

    await windowManager.setFullScreen(true);
    await windowManager.setAlwaysOnTop(true);
    await windowManager.show();
    await windowManager.focus();

    _overlayTimer?.cancel();
    _overlayTimer = Timer(const Duration(seconds: 10), () {
      _hideFullScreenPunch();
    });
  }

  void _hideFullScreenPunch() async {
    if (mounted) {
      setState(() {
        _showOverlay = false;
      });
    }
    await windowManager.setFullScreen(false);
    await windowManager.setAlwaysOnTop(false);
  }

  String _enumToString(dynamic enumItem) {
    return enumItem.toString().split('.')[1];
  }

  @override
  void dispose() {
    trayManager.removeListener(this);
    windowManager.removeListener(this);
    _channel?.sink.close();
    _urlController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final mainScaffold = Scaffold(
      appBar: AppBar(
        title: const Text('Attendance Notifier'),
        backgroundColor: Theme.of(context).colorScheme.inversePrimary,
        actions: [
          Row(
            children: [
              Text(
                _isConnected ? 'Connected' : 'Disconnected',
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                  color: _isConnected ? Colors.green.shade800 : Colors.red.shade800,
                ),
              ),
              const SizedBox(width: 8),
              Icon(
                _isConnected ? Icons.wifi : Icons.wifi_off,
                color: _isConnected ? Colors.green.shade800 : Colors.red.shade800,
              ),
              const SizedBox(width: 16),
            ],
          )
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          children: [
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _urlController,
                    decoration: const InputDecoration(
                      labelText: 'WebSocket URL',
                      border: OutlineInputBorder(),
                    ),
                    enabled: !_isConnected,
                  ),
                ),
                const SizedBox(width: 16),
                ElevatedButton(
                  onPressed: _isConnected ? _disconnect : _connect,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: _isConnected ? Colors.red : Colors.green,
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
                  ),
                  child: Text(_isConnected ? 'Disconnect' : 'Connect'),
                ),
              ],
            ),
            const SizedBox(height: 24),
            const Text(
              'Recent Punches',
              style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
            ),
            const Divider(),
            Expanded(
              child: _punches.isEmpty
                  ? const Center(child: Text('No punches received yet.'))
                  : ListView.builder(
                      itemCount: _punches.length,
                      itemBuilder: (context, index) {
                        final punch = _punches[index];
                        return Card(
                          child: ListTile(
                            leading: const CircleAvatar(child: Icon(Icons.person)),
                            title: Text(punch['name'] ?? ''),
                            subtitle: Text('ID: ${punch['employee_id']} - ${punch['time']}'),
                          ),
                        );
                      },
                    ),
            ),
          ],
        ),
      ),
    );

    return Stack(
      children: [
        mainScaffold,
        if (_showOverlay && _currentPunchData != null)
          Positioned.fill(
            child: Material(
              color: Colors.blue.shade900.withOpacity(0.95),
              child: SafeArea(
                child: Column(
                  children: [
                    Align(
                      alignment: Alignment.topRight,
                      child: Padding(
                        padding: const EdgeInsets.all(16.0),
                        child: ElevatedButton.icon(
                          icon: const Icon(Icons.skip_next),
                          label: const Text('Skip', style: TextStyle(fontSize: 18)),
                          style: ElevatedButton.styleFrom(
                            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
                          ),
                          onPressed: () {
                            _overlayTimer?.cancel();
                            _hideFullScreenPunch();
                          },
                        ),
                      ),
                    ),
                    Expanded(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          const Icon(Icons.check_circle, size: 120, color: Colors.greenAccent),
                          const SizedBox(height: 32),
                          const Text(
                            'Punch Successful!',
                            style: TextStyle(fontSize: 48, fontWeight: FontWeight.bold, color: Colors.white),
                          ),
                          const SizedBox(height: 24),
                          Text(
                            _currentPunchData!['name']!,
                            style: const TextStyle(fontSize: 64, fontWeight: FontWeight.w900, color: Colors.white),
                            textAlign: TextAlign.center,
                          ),
                          const SizedBox(height: 16),
                          Text(
                            'Employee ID: ${_currentPunchData!['empId']}',
                            style: const TextStyle(fontSize: 32, color: Colors.white70),
                          ),
                          const SizedBox(height: 16),
                          Text(
                            _currentPunchData!['time']!,
                            style: const TextStyle(fontSize: 24, color: Colors.white54),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
      ],
    );
  }
}
