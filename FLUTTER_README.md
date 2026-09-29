# Flutter Manager App - Complete Implementation Guide

## Project Overview

Flutter application for managing door locks via Bhumijo Gateway Service. Provides lock/unlock control, user access management, and real-time status updates.

## What's Included

1. **FLUTTER_MANAGER_APP.md** (50+ KB)
   - Complete architecture
   - Project structure
   - All code files with implementations
   - BLoC/Provider state management
   - API integration
   - Testing guides
   - Deployment instructions

2. **FLUTTER_UI_DESIGN.md** (40+ KB)
   - Lock/Unlock screen design
   - Color schemes
   - Reusable widgets
   - Multiple design options
   - Responsive layouts
   - Animations
   - Theme configuration

## Quick Start

### 1. Create Flutter Project
```bash
flutter create flutter_manager_app
cd flutter_manager_app
```

### 2. Add Dependencies
Copy `pubspec.yaml` from FLUTTER_MANAGER_APP.md section into your project.

```bash
flutter pub get
```

### 3. Create Directory Structure
```bash
mkdir -p lib/{config,data/{models,services,repositories},logic/{bloc,providers},presentation/{screens,widgets},utils}
mkdir -p test
```

### 4. Create Core Files
Follow FLUTTER_MANAGER_APP.md and create:
- Models (lock_state_model.dart, relay_model.dart, user_access_model.dart)
- Services (gateway_service.dart)
- Repository (manager_repository.dart)
- BLoC (manager_bloc.dart)
- UI Screens (lock_unlock_screen.dart)

### 5. Configure Gateway Connection
In main.dart:
```dart
final gatewayService = GatewayService(baseUrl: 'http://192.168.1.100:5454');
```

### 6. Run App
```bash
flutter run
```

## File Locations

```
FLUTTER_MANAGER_APP.md
├── Architecture Diagram
├── Project Structure
├── pubspec.yaml (full content)
├── Core Implementation
│   ├── Models (3 data classes)
│   ├── Services (GatewayService)
│   ├── Repositories (ManagerRepository)
│   ├── BLoC (Events, States, BLoC class)
│   └── UI (Lock/Unlock Screen)
├── State Management Options
├── Features Roadmap
├── Testing Guide
└── Deployment

FLUTTER_UI_DESIGN.md
├── Screen Layout Diagram
├── Color Scheme
├── Widget Components (3 ready-to-use widgets)
├── Full Screen Implementation
├── Alternative Designs (2 options)
├── Responsive Design
├── Animation Examples
├── Theme Configuration
└── Implementation Tips
```

## Key Features

✅ **Lock/Unlock Control**
- Real-time status display
- Animated status circle
- Lock/Unlock buttons with states
- Visual feedback

✅ **Error Handling**
- Network error detection
- User-friendly error messages
- Retry mechanisms
- Fallback options

✅ **State Management**
- BLoC pattern implementation
- Event-driven architecture
- Clean separation of concerns
- Easy to test

✅ **UI/UX**
- Material Design 3
- Responsive layouts
- Smooth animations
- Accessible design

## Integration Steps

### Step 1: Setup DI (Dependency Injection)
```dart
void main() {
  final gatewayService = GatewayService(baseUrl: 'http://192.168.1.100:5454');
  final managerRepository = ManagerRepository(gatewayService: gatewayService);
  runApp(MyApp(managerRepository: managerRepository));
}
```

### Step 2: Create BLoC Provider
```dart
BlocProvider<ManagerBloc>(
  create: (_) => ManagerBloc(repository: managerRepository),
  child: const LockUnlockScreen(),
)
```

### Step 3: Build UI
Use widgets from FLUTTER_UI_DESIGN.md:
- LockStatusDisplay
- LockControlButtons
- LockStatusBar

### Step 4: Handle Events
```dart
// Lock
context.read<ManagerBloc>().add(const LockRequested());

// Unlock
context.read<ManagerBloc>().add(const UnlockRequested());

// Refresh Status
context.read<ManagerBloc>().add(const StatusRefreshRequested());
```

## Gateway Integration Points

### API Endpoints Called

```
POST /relay/control
  └─ Lock/Unlock door
  └─ Payload: {"relay": 1, "state": 0/1}

GET /relay/status
  └─ Get current lock status
  └─ Response: {"relays": {"1": true/false}}

POST /manager
  └─ Log manager events
  └─ Payload: {"card_id", "gender", "button_id"}

GET /health
  └─ Check gateway availability
```

### Network Configuration

```dart
Dio _dio = Dio(BaseOptions(
  baseUrl: 'http://192.168.1.100:5454',
  connectTimeout: Duration(seconds: 10),
  receiveTimeout: Duration(seconds: 10),
));
```

## State Flow Diagram

```
User Action (Tap Button)
    ↓
ManagerBloc receives Event (LockRequested/UnlockRequested)
    ↓
ManagerBloc emits ManagerLoading state
    ↓
ManagerRepository calls GatewayService
    ↓
GatewayService sends HTTP request to Linux Gateway
    ↓
Linux Gateway controls KC868 relay
    ↓
Response returned to Flutter app
    ↓
ManagerBloc emits LockStateChanged state
    ↓
UI rebuilds with new lock state
    ↓
User sees LOCKED/UNLOCKED status update
```

## Testing

### Unit Test Template
```dart
void main() {
  group('ManagerBloc', () {
    late MockManagerRepository mockRepository;
    late ManagerBloc managerBloc;

    setUp(() {
      mockRepository = MockManagerRepository();
      managerBloc = ManagerBloc(repository: mockRepository);
    });

    test('emits correct state on unlock', () async {
      when(mockRepository.unlock()).thenAnswer((_) async => true);
      
      managerBloc.add(const UnlockRequested());
      
      await expectLater(
        managerBloc.stream,
        emits(isA<LockStateChanged>()),
      );
    });
  });
}
```

### Widget Test Template
```dart
void main() {
  testWidgets('Lock/Unlock screen displays correctly', (WidgetTester tester) async {
    await tester.pumpWidget(const MyApp());
    
    expect(find.text('Door Lock Control'), findsOneWidget);
    expect(find.byIcon(Icons.lock), findsWidgets);
  });
}
```

## Troubleshooting

### Gateway Not Connecting
```bash
# Check gateway running
curl http://192.168.1.100:5454/health

# Verify network
adb shell ping 192.168.1.100

# Check logs
flutter logs | grep "GatewayService"
```

### UI Not Updating
- Verify BLoC is emitting states
- Check BlocBuilder/BlocListener setup
- Ensure events are dispatched

### Slow Response
- Check network latency
- Verify gateway performance
- Increase timeout values

## Performance Optimization

1. **Use const constructors** - Prevents unnecessary rebuilds
2. **Lazy load screens** - Load screens on demand
3. **Cache images** - Use image_cache
4. **Optimize BLoC** - Only rebuild affected widgets
5. **Use BlocListener** - For side effects only

## Security Checklist

- [ ] Use HTTPS in production
- [ ] Add API authentication (Bearer token)
- [ ] Encrypt sensitive data
- [ ] Implement biometric authentication
- [ ] Add request signing
- [ ] Validate all inputs
- [ ] Use secure storage for tokens

## Deployment

### Android
```bash
flutter build apk --release
# Output: build/app/outputs/apk/release/app-release.apk

# Or bundle for Play Store
flutter build appbundle --release
# Output: build/app/outputs/bundle/release/app-release.aab
```

### iOS
```bash
flutter build ios --release
# Output: build/ios/iphoneos/Runner.app

# Or create archive
flutter build ios --release --no-codesign
```

## Dependency Versions (as of Aug 2024)

- flutter_bloc: 8.1.3
- provider: 6.0.0
- dio: 5.3.1
- hive_flutter: 1.1.0
- mobile_scanner: 3.5.0
- flutter_local_notifications: 15.1.1

Check pubspec.yaml for latest versions.

## Project Roadmap

### Phase 1 (MVP) - CURRENT
- Lock/Unlock functionality
- Real-time status display
- Basic error handling
- Settings screen

### Phase 2 (Features)
- User access management
- Activity logs
- QR code scanning
- Multi-door support
- Analytics dashboard

### Phase 3 (Advanced)
- Scheduling
- Automation rules
- Integration with other systems
- Mobile notifications
- Offline mode

## Support & Documentation

### Main Guides
1. **FLUTTER_MANAGER_APP.md** - Full implementation details
2. **FLUTTER_UI_DESIGN.md** - UI/UX design guide
3. **INTEGRATION_GUIDE.md** (Android) - Integration patterns
4. **REMOTE_GATEWAY_SETUP.md** (Android) - Gateway configuration

### Linux Gateway
- **README.md** - Linux gateway overview
- **SETUP.md** - Complete setup guide
- **QUICKSTART.md** - Quick start reference

### Other Integration Points
- **FLUTTER_MANAGER_APP.md** - This Flutter app
- **Android App** - bhumijo-gateway-android
- **Linux Gateway** - bhumijo-gateway-service
- **KC868-A4S Relay** - Hardware controller

## Getting Help

1. Check logs:
   ```bash
   flutter logs
   ```

2. Test gateway:
   ```bash
   curl http://192.168.1.100:5454/health
   ```

3. Debug BLoC:
   ```bash
   // In code
   Bloc.observer = SimpleBlocObserver();
   ```

4. Check network:
   ```bash
   adb shell ping 192.168.1.100
   ```

## Next Steps

1. Review FLUTTER_MANAGER_APP.md for full architecture
2. Review FLUTTER_UI_DESIGN.md for UI implementation
3. Create Flutter project
4. Implement core files
5. Test with Linux gateway
6. Deploy to app stores

## File Checklist

- [ ] Created Flutter project
- [ ] Added all dependencies (pubspec.yaml)
- [ ] Created models directory and files
- [ ] Created services/gateway_service.dart
- [ ] Created repositories/manager_repository.dart
- [ ] Created logic/bloc/manager_bloc.dart
- [ ] Created presentation/screens/lock_unlock_screen.dart
- [ ] Created presentation/widgets/ (3+ widgets)
- [ ] Configured DI in main.dart
- [ ] Tested with running Linux gateway
- [ ] Deployed to test device
- [ ] Ready for production

---

**Ready to build?** Start with FLUTTER_MANAGER_APP.md!
