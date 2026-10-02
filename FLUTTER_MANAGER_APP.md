# Flutter Manager App - Lock/Unlock Implementation

> **Updated for the facility node** (`facility-node/`). The app is now the **Facility app**. It talks to the Pi at
> `http://192.168.10.104:5454` with exactly two endpoints:
>
> | Endpoint | Body | Result |
> |---|---|---|
> | `POST /facility/open` | `{"authCode": "...", "door": "male" \| "female"}` | 200 opened · 403 wrong code · 400 unknown door · 429 too many tries · 502 relay error |
> | `GET /health` | — | `doors.<name>.open`, queue, last sync |
>
> - **No lock action:** each door relocks by itself through its timer module.
> - **Auth Code, not Facility ID:** the app authenticates with the per-facility Auth Code.
> - **Removed endpoints:** `/relay/control`, `/relay/status` and `/manager` are gone.
>
> See `facility-node/SETUP.md` section 9.

## Overview

Flutter application for managing gateway access control. Features lock/unlock, relay control, QR scanning, user access management, real-time status.

## Architecture

```
┌─────────────────────────────┐
│   Flutter Manager App       │
│   (iOS/Android)             │
├──────────┬──────────────────┤
│ UI Layer │ Screens:         │
│          ├─ Lock/Unlock     │
│          ├─ Relay Control   │
│          ├─ User Access     │
│          ├─ QR Scanner      │
│          └─ Reports         │
├──────────┼──────────────────┤
│ Logic    │ BLoC/Provider:   │
│ Layer    ├─ ManagerBloc     │
│          ├─ RelayBloc       │
│          ├─ AccessBloc      │
│          └─ QRBloc          │
├──────────┼──────────────────┤
│ Data     │ Repositories:    │
│ Layer    ├─ ManagerRepo     │
│          ├─ RelayRepo       │
│          ├─ UserRepo        │
│          └─ AuthRepo        │
└──────────┴──────────────────┘
            │
            ▼
┌─────────────────────────────┐
│  API Layer (Dio/Http)       │
├─────────────────────────────┤
│ POST /facility/open         │
│ GET  /health                │
└─────────────────────────────┘
            │
            ▼
┌─────────────────────────────┐
│  Facility Node (Pi 5)       │
│  192.168.10.104:5454        │
└─────────────────────────────┘
            │
            ▼
┌─────────────────────────────┐
│  KC868-A4S Relay Controller │
└─────────────────────────────┘
```

## Project Structure

```
flutter_manager_app/
├── lib/
│   ├── main.dart
│   ├── config/
│   │   ├── app_config.dart
│   │   ├── theme.dart
│   │   └── constants.dart
│   ├── data/
│   │   ├── models/
│   │   │   ├── manager_model.dart
│   │   │   ├── relay_model.dart
│   │   │   ├── user_access_model.dart
│   │   │   └── lock_state_model.dart
│   │   ├── services/
│   │   │   ├── api_service.dart
│   │   │   ├── gateway_service.dart
│   │   │   └── auth_service.dart
│   │   └── repositories/
│   │       ├── manager_repository.dart
│   │       ├── relay_repository.dart
│   │       └── access_repository.dart
│   ├── logic/
│   │   ├── bloc/
│   │   │   ├── manager_bloc.dart
│   │   │   ├── relay_bloc.dart
│   │   │   ├── access_bloc.dart
│   │   │   └── qr_bloc.dart
│   │   └── providers/
│   │       ├── manager_provider.dart
│   │       └── relay_provider.dart
│   ├── presentation/
│   │   ├── screens/
│   │   │   ├── splash_screen.dart
│   │   │   ├── login_screen.dart
│   │   │   ├── lock_unlock_screen.dart
│   │   │   ├── relay_control_screen.dart
│   │   │   ├── access_list_screen.dart
│   │   │   ├── qr_scanner_screen.dart
│   │   │   ├── reports_screen.dart
│   │   │   └── settings_screen.dart
│   │   └── widgets/
│   │       ├── lock_button.dart
│   │       ├── relay_card.dart
│   │       ├── user_access_card.dart
│   │       ├── status_indicator.dart
│   │       └── custom_app_bar.dart
│   └── utils/
│       ├── logger.dart
│       ├── error_handler.dart
│       └── validators.dart
├── pubspec.yaml
└── test/
    ├── widget_test.dart
    └── unit_test.dart
```

## pubspec.yaml

```yaml
name: flutter_manager_app
description: Bhumijo Manager - Gateway Lock/Unlock Control

environment:
  sdk: '>=3.0.0 <4.0.0'
  flutter: '>=3.10.0'

dependencies:
  flutter:
    sdk: flutter
  cupertino_icons: ^1.0.2
  
  # State Management
  flutter_bloc: ^8.1.3
  provider: ^6.0.0
  
  # Networking
  dio: ^5.3.1
  http: ^1.1.0
  connectivity_plus: ^5.0.0
  
  # Data/Storage
  hive: ^2.2.3
  hive_flutter: ^1.1.0
  shared_preferences: ^2.2.0
  
  # UI/UX
  google_fonts: ^5.1.0
  iconsax: ^0.0.8
  lottie: ^2.6.0
  flutter_screenutil: ^5.9.0
  
  # QR Scanning
  mobile_scanner: ^3.5.0
  qr_code_scanner: ^1.0.1
  
  # Utils
  intl: ^0.18.1
  logger: ^2.0.1
  equatable: ^2.0.5
  get_it: ^7.6.0
  
  # Camera (for QR)
  camera: ^0.10.5+2
  image_picker: ^1.0.4
  
  # Local notifications
  flutter_local_notifications: ^15.1.1

dev_dependencies:
  flutter_test:
    sdk: flutter
  flutter_lints: ^2.0.0
  mockito: ^5.4.0
  build_runner: ^2.4.6

flutter:
  uses-material-design: true
  assets:
    - assets/images/
    - assets/animations/
    - assets/icons/
  fonts:
    - family: CustomFont
      fonts:
        - asset: assets/fonts/custom_regular.ttf
```

## Core Files Implementation

### 1. Models (data/models/)

#### lock_state_model.dart
```dart
import 'package:equatable/equatable.dart';

enum LockStatus {
  locked,
  unlocked,
  unlocking,
  locking,
  error
}

class LockState extends Equatable {
  final LockStatus status;
  final DateTime? lastChanged;
  final String? error;
  final int? relayNumber;
  final String? changedBy;

  const LockState({
    required this.status,
    this.lastChanged,
    this.error,
    this.relayNumber = 1,
    this.changedBy,
  });

  bool get isLocked => status == LockStatus.locked;
  bool get isUnlocked => status == LockStatus.unlocked;
  bool get isLoading => status == LockStatus.locking || status == LockStatus.unlocking;

  LockState copyWith({
    LockStatus? status,
    DateTime? lastChanged,
    String? error,
    int? relayNumber,
    String? changedBy,
  }) {
    return LockState(
      status: status ?? this.status,
      lastChanged: lastChanged ?? this.lastChanged,
      error: error,
      relayNumber: relayNumber ?? this.relayNumber,
      changedBy: changedBy ?? this.changedBy,
    );
  }

  @override
  List<Object?> get props => [status, lastChanged, error, relayNumber, changedBy];
}
```

#### relay_model.dart
```dart
import 'package:equatable/equatable.dart';

class RelayStatus extends Equatable {
  final int relayNumber;
  final bool isOn;
  final DateTime? lastUpdated;
  final String? name;

  const RelayStatus({
    required this.relayNumber,
    required this.isOn,
    this.lastUpdated,
    this.name,
  });

  RelayStatus copyWith({
    int? relayNumber,
    bool? isOn,
    DateTime? lastUpdated,
    String? name,
  }) {
    return RelayStatus(
      relayNumber: relayNumber ?? this.relayNumber,
      isOn: isOn ?? this.isOn,
      lastUpdated: lastUpdated ?? this.lastUpdated,
      name: name ?? this.name,
    );
  }

  @override
  List<Object?> get props => [relayNumber, isOn, lastUpdated, name];
}
```

#### user_access_model.dart
```dart
import 'package:equatable/equatable.dart';

class UserAccess extends Equatable {
  final String userId;
  final String userName;
  final String cardId;
  final bool hasAccess;
  final DateTime? lastAccess;
  final String? accessLevel;

  const UserAccess({
    required this.userId,
    required this.userName,
    required this.cardId,
    required this.hasAccess,
    this.lastAccess,
    this.accessLevel,
  });

  @override
  List<Object?> get props => [userId, userName, cardId, hasAccess, lastAccess, accessLevel];
}
```

### 2. API Service (data/services/gateway_service.dart)

```dart
import 'package:dio/dio.dart';
import '../models/relay_model.dart';

enum UnlockResult { opened, wrongAuthCode, unknownDoor, tooManyAttempts, relayError }

class GatewayService {
  late Dio _dio;
  final String baseUrl;
  final String authCode;   // per-facility Auth Code (NOT the Facility ID)

  GatewayService({required this.baseUrl, required this.authCode}) {
    _dio = Dio(BaseOptions(
      baseUrl: baseUrl,
      connectTimeout: Duration(seconds: 10),
      receiveTimeout: Duration(seconds: 10),
      headers: {
        'Content-Type': 'application/json',
      },
      // Let 4xx/5xx through so we can map them to messages below
      validateStatus: (_) => true,
    ));

    _dio.interceptors.add(LogInterceptor());
  }

  /// Unlock one door ("male" / "female"). The door relocks by itself.
  Future<UnlockResult> unlockDoor(String door) async {
    try {
      final response = await _dio.post(
        '/facility/open',
        data: {'authCode': authCode, 'door': door},
      );
      switch (response.statusCode) {
        case 200: return UnlockResult.opened;
        case 403: return UnlockResult.wrongAuthCode;
        case 400: return UnlockResult.unknownDoor;
        case 429: return UnlockResult.tooManyAttempts;
        default:  return UnlockResult.relayError;
      }
    } on DioException catch (e) {
      throw _handleError(e);
    }
  }

  /// Per-door open/closed state from the node's /health.
  Future<Map<String, bool>> doorStates() async {
    final response = await _dio.get('/health');
    final doors = (response.data['doors'] as Map<String, dynamic>);
    return doors.map((name, d) => MapEntry(name, d['open'] == true));
  }

  // Health check
  Future<bool> healthCheck() async {
    try {
      final response = await _dio.get('/health');
      return response.statusCode == 200;
    } on DioException {
      return false;
    }
  }

  String _handleError(DioException e) {
    // Network-level errors only; HTTP status codes are handled in unlockDoor
    if (e.type == DioExceptionType.connectionTimeout) {
      return 'Connection timeout';
    } else if (e.type == DioExceptionType.receiveTimeout) {
      return 'Request timeout';
    } else if (e.response?.statusCode == 404) {
      return 'Endpoint not found';
    } else if (e.response?.statusCode == 500) {
      return 'Server error';
    }
    return e.message ?? 'Unknown error';
  }
}
```

### 3. BLoC (logic/bloc/manager_bloc.dart)

```dart
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:equatable/equatable.dart';
import '../../data/models/lock_state_model.dart';
import '../../data/repositories/manager_repository.dart';

// Events
abstract class ManagerEvent extends Equatable {
  const ManagerEvent();
}

// No LockRequested: doors relock automatically via their timer modules.
class UnlockRequested extends ManagerEvent {
  final String door;   // "male" | "female"
  const UnlockRequested(this.door);

  @override
  List<Object?> get props => [door];
}

class StatusRefreshRequested extends ManagerEvent {
  final String door;
  const StatusRefreshRequested(this.door);

  @override
  List<Object?> get props => [door];
}

// States
abstract class ManagerState extends Equatable {
  const ManagerState();
}

class ManagerInitial extends ManagerState {
  const ManagerInitial();

  @override
  List<Object?> get props => [];
}

class ManagerLoading extends ManagerState {
  const ManagerLoading();

  @override
  List<Object?> get props => [];
}

class LockStateChanged extends ManagerState {
  final LockState lockState;

  const LockStateChanged(this.lockState);

  @override
  List<Object?> get props => [lockState];
}

class ManagerError extends ManagerState {
  final String message;

  const ManagerError(this.message);

  @override
  List<Object?> get props => [message];
}

// BLoC
class ManagerBloc extends Bloc<ManagerEvent, ManagerState> {
  final ManagerRepository repository;
  LockState currentLockState = const LockState(status: LockStatus.locked);

  ManagerBloc({required this.repository}) : super(const ManagerInitial()) {
    on<UnlockRequested>(_onUnlockRequested);
    on<StatusRefreshRequested>(_onStatusRefreshRequested);
  }

  Future<void> _onUnlockRequested(
    UnlockRequested event,
    Emitter<ManagerState> emit,
  ) async {
    emit(const ManagerLoading());
    try {
      final result = await repository.unlock(event.door);
      switch (result) {
        case UnlockResult.opened:
          currentLockState = currentLockState.copyWith(
            status: LockStatus.unlocked,
            lastChanged: DateTime.now(),
          );
          emit(LockStateChanged(currentLockState));
        case UnlockResult.wrongAuthCode:
          emit(const ManagerError('Wrong Auth Code - check app settings'));
        case UnlockResult.tooManyAttempts:
          emit(const ManagerError('Too many attempts - wait a minute'));
        case UnlockResult.unknownDoor:
          emit(const ManagerError('Unknown door'));
        case UnlockResult.relayError:
          emit(const ManagerError('Door controller offline'));
      }
    } catch (e) {
      emit(ManagerError(e.toString()));
    }
  }

  Future<void> _onStatusRefreshRequested(
    StatusRefreshRequested event,
    Emitter<ManagerState> emit,
  ) async {
    try {
      final status = await repository.getStatus(event.door);
      emit(LockStateChanged(status));
    } catch (e) {
      emit(ManagerError(e.toString()));
    }
  }
}
```

### 4. Repository (data/repositories/manager_repository.dart)

```dart
import '../models/lock_state_model.dart';
import '../services/gateway_service.dart';

class ManagerRepository {
  final GatewayService gatewayService;

  ManagerRepository({required this.gatewayService});

  // No lock(): the timer module relocks each door automatically.
  Future<UnlockResult> unlock(String door) async {
    return await gatewayService.unlockDoor(door);   // "male" / "female"
  }

  Future<LockState> getStatus(String door) async {
    try {
      final states = await gatewayService.doorStates();
      return LockState(
        status: states[door] == true ? LockStatus.unlocked : LockStatus.locked,
        lastChanged: DateTime.now(),
      );
    } catch (e) {
      return const LockState(
        status: LockStatus.error,
        error: 'Failed to get status',
      );
    }
  }
}
```

## UI Implementation

### Main Lock/Unlock Screen (presentation/screens/lock_unlock_screen.dart)

```dart
import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:lottie/lottie.dart';
import '../../logic/bloc/manager_bloc.dart';
import '../../data/models/lock_state_model.dart';

class LockUnlockScreen extends StatefulWidget {
  const LockUnlockScreen({Key? key}) : super(key: key);

  @override
  State<LockUnlockScreen> createState() => _LockUnlockScreenState();
}

class _LockUnlockScreenState extends State<LockUnlockScreen> {
  @override
  void initState() {
    super.initState();
    context.read<ManagerBloc>().add(const StatusRefreshRequested('male'));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Door Lock Control'),
        centerTitle: true,
        elevation: 0,
      ),
      body: BlocBuilder<ManagerBloc, ManagerState>(
        builder: (context, state) {
          return Center(
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                // Status Display
                _buildStatusSection(state),
                const SizedBox(height: 40),
                // Lock/Unlock Buttons
                _buildControlButtons(context, state),
                const SizedBox(height: 30),
                // Last Changed
                _buildLastChangedInfo(state),
              ],
            ),
          );
        },
      ),
    );
  }

  Widget _buildStatusSection(ManagerState state) {
    if (state is LockStateChanged) {
      final lockState = state.lockState;
      final isLocked = lockState.isLocked;

      return Column(
        children: [
          Container(
            width: 200,
            height: 200,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: isLocked ? Colors.red.shade100 : Colors.green.shade100,
              boxShadow: [
                BoxShadow(
                  color: (isLocked ? Colors.red : Colors.green).withOpacity(0.3),
                  blurRadius: 20,
                  spreadRadius: 5,
                ),
              ],
            ),
            child: Center(
              child: Icon(
                isLocked ? Icons.lock : Icons.lock_open,
                size: 80,
                color: isLocked ? Colors.red : Colors.green,
              ),
            ),
          ),
          const SizedBox(height: 20),
          Text(
            isLocked ? 'LOCKED' : 'UNLOCKED',
            style: TextStyle(
              fontSize: 32,
              fontWeight: FontWeight.bold,
              color: isLocked ? Colors.red : Colors.green,
            ),
          ),
        ],
      );
    } else if (state is ManagerLoading) {
      return Column(
        children: [
          const SizedBox(
            width: 150,
            height: 150,
            child: CircularProgressIndicator(strokeWidth: 4),
          ),
          const SizedBox(height: 20),
          const Text('Processing...'),
        ],
      );
    } else if (state is ManagerError) {
      return Column(
        children: [
          Icon(Icons.error_outline, size: 80, color: Colors.red),
          const SizedBox(height: 20),
          Text(
            'Error: ${state.message}',
            style: const TextStyle(color: Colors.red),
          ),
        ],
      );
    }

    return const Text('Loading status...');
  }

  Widget _buildControlButtons(BuildContext context, ManagerState state) {
    final isLoading = state is ManagerLoading;

    // One unlock button per door; doors relock by themselves
    Widget unlockButton(String door, String label) => ElevatedButton.icon(
          onPressed: isLoading
              ? null
              : () => context.read<ManagerBloc>().add(UnlockRequested(door)),
          icon: const Icon(Icons.lock_open),
          label: Text(label),
          style: ElevatedButton.styleFrom(
            backgroundColor: Colors.green,
            disabledBackgroundColor: Colors.grey,
            padding: const EdgeInsets.symmetric(horizontal: 30, vertical: 15),
          ),
        );

    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        unlockButton('male', 'UNLOCK MALE'),
        const SizedBox(width: 20),
        unlockButton('female', 'UNLOCK FEMALE'),
      ],
    );
  }

  Widget _buildLastChangedInfo(ManagerState state) {
    if (state is LockStateChanged && state.lockState.lastChanged != null) {
      return Text(
        'Last changed: ${state.lockState.lastChanged.toString().substring(0, 19)}',
        style: const TextStyle(color: Colors.grey, fontSize: 12),
      );
    }
    return const SizedBox.shrink();
  }
}
```

## Integration Steps

### 1. Setup DI (main.dart)

```dart
void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  
  // Initialize services
  final gatewayService = GatewayService(baseUrl: 'http://192.168.10.104:5454', authCode: settings.authCode);
  final managerRepository = ManagerRepository(gatewayService: gatewayService);
  
  runApp(MyApp(managerRepository: managerRepository));
}

class MyApp extends StatelessWidget {
  final ManagerRepository managerRepository;

  const MyApp({required this.managerRepository});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Bhumijo Manager',
      theme: ThemeData(primarySwatch: Colors.blue),
      home: BlocProvider(
        create: (_) => ManagerBloc(repository: managerRepository),
        child: const LockUnlockScreen(),
      ),
    );
  }
}
```

### 2. State Management Options

**Option A: BLoC (Recommended)**
```dart
BlocProvider<ManagerBloc>(
  create: (_) => ManagerBloc(repository: repository),
  child: const LockUnlockScreen(),
)
```

**Option B: Provider**
```dart
ChangeNotifierProvider(
  create: (_) => ManagerProvider(repository: repository),
  child: const LockUnlockScreen(),
)
```

### 3. Real-time Updates

```dart
// Periodic status check
Timer.periodic(Duration(seconds: 5), (_) {
  context.read<ManagerBloc>().add(const StatusRefreshRequested('male'));
});
```

## Features Roadmap

### Phase 1 (MVP)
- [x] Lock/Unlock button with status
- [x] Real-time status updates
- [ ] Error handling & notifications
- [ ] Settings screen

### Phase 2
- [ ] User access management
- [ ] Activity logs
- [ ] QR code scanner
- [ ] Multi-door support

### Phase 3
- [ ] Analytics dashboard
- [ ] User management
- [ ] Advanced scheduling
- [ ] Integration with other systems

## API Endpoints Used

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/facility/open` | POST | Unlock a door: `{authCode, door}` |
| `/health` | GET | Node health + per-door open state |

## Error Handling

```dart
// Network errors
try {
  await gatewayService.unlockDoor('male');
} on DioException catch (e) {
  if (e.type == DioExceptionType.connectionTimeout) {
    showError('Connection timeout');
  } else if (e.response?.statusCode == 404) {
    showError('Gateway not found');
  }
}

// BLoC errors
if (state is ManagerError) {
  showSnackBar(state.message);
}
```

## Testing

### Unit Tests
```dart
void main() {
  group('ManagerBloc', () {
    late MockManagerRepository mockRepository;
    late ManagerBloc managerBloc;

    setUp(() {
      mockRepository = MockManagerRepository();
      managerBloc = ManagerBloc(repository: mockRepository);
    });

    test('unlock sets correct lock state', () async {
      when(mockRepository.unlock('male')).thenAnswer((_) async => UnlockResult.opened);
      
      managerBloc.add(const UnlockRequested('male'));
      
      await expectLater(
        managerBloc.stream,
        emits(isA<LockStateChanged>()),
      );
    });
  });
}
```

### Widget Tests
```dart
void main() {
  testWidgets('Lock button is disabled when already locked', (WidgetTester tester) async {
    await tester.pumpWidget(TestApp());
    
    expect(find.byIcon(Icons.lock), findsOneWidget);
  });
}
```

## Deployment

### Android
```bash
flutter build apk --release
# or
flutter build appbundle --release
```

### iOS
```bash
flutter build ios --release
```

## Configuration

### gateway_config.dart
```dart
class GatewayConfig {
  static const String gatewayUrl = '192.168.10.104';   // facility Pi
  static const int gatewayPort = 5454;
  static const List<String> doors = ['male', 'female'];
  // Auth Code comes from app settings (per facility), never hard-coded
  static const String appVersion = '1.0.0';
}
```

## Security

- [ ] Add API authentication (Bearer token)
- [ ] Encrypt sensitive data
- [ ] Implement biometric lock
- [ ] Add audit logging
- [ ] Use HTTPS for production

## Performance

- [ ] Optimize BLoC event handling
- [ ] Cache relay status locally
- [ ] Implement lazy loading
- [ ] Add pagination for lists
- [ ] Use image caching

## Troubleshooting

**Gateway not connecting:**
```bash
# Check gateway running
curl http://192.168.10.104:5454/health

# Check network connectivity
adb shell ping 192.168.10.104
```

**UI not updating:**
- Check BLoC state emission
- Verify BlocListener/BlocBuilder setup
- Check event dispatch

**Slow response:**
- Increase timeout values
- Check network latency
- Verify gateway performance

## Next Steps

1. Create Flutter project: `flutter create flutter_manager_app`
2. Add dependencies to pubspec.yaml
3. Implement models and services
4. Implement BLoC
5. Build UI screens
6. Integrate with gateway
7. Test thoroughly
8. Deploy to app stores