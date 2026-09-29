# Flutter Manager App - UI Design Guide

## Lock/Unlock Screen Design

### Layout Structure

```
┌─────────────────────────────────┐
│         APP BAR                 │
│    Door Lock Control            │
├─────────────────────────────────┤
│                                 │
│         STATUS CIRCLE           │
│     [LOCKED] or [UNLOCKED]       │
│     Big Lock/Unlock Icon         │
│     Circle with shadow           │
│                                 │
├─────────────────────────────────┤
│                                 │
│      CONTROL BUTTONS            │
│   [LOCK]    [UNLOCK]            │
│   (RED)     (GREEN)             │
│                                 │
├─────────────────────────────────┤
│                                 │
│    Last changed: HH:MM          │
│                                 │
└─────────────────────────────────┘
```

## Color Scheme

```
PRIMARY COLORS:
- Locked: Red (#E53935) / Light Red (#FFEBEE)
- Unlocked: Green (#43A047) / Light Green (#E8F5E9)
- Neutral: Grey (#757575)
- Background: White (#FFFFFF)

ACCENT COLORS:
- Shadow: Black (20% opacity)
- Text: Dark Grey (#212121)
- Success: Green (#4CAF50)
- Error: Red (#F44336)
```

## Widget Components

### 1. Lock Status Display Widget

```dart
class LockStatusDisplay extends StatelessWidget {
  final bool isLocked;
  final VoidCallback onRefresh;
  
  const LockStatusDisplay({
    required this.isLocked,
    required this.onRefresh,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        // Animated Circle
        TweenAnimationBuilder<double>(
          tween: Tween(begin: 0, end: 1),
          duration: Duration(milliseconds: 500),
          builder: (context, value, child) {
            return Transform.scale(
              scale: 0.8 + (value * 0.2),
              child: Container(
                width: 200,
                height: 200,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: isLocked 
                      ? Colors.red.shade100 
                      : Colors.green.shade100,
                  boxShadow: [
                    BoxShadow(
                      color: (isLocked ? Colors.red : Colors.green)
                          .withOpacity(0.3 * value),
                      blurRadius: 20 * value,
                      spreadRadius: 5 * value,
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
            );
          },
        ),
        SizedBox(height: 20),
        // Status Text
        Text(
          isLocked ? 'LOCKED' : 'UNLOCKED',
          style: TextStyle(
            fontSize: 32,
            fontWeight: FontWeight.bold,
            color: isLocked ? Colors.red : Colors.green,
            letterSpacing: 2,
          ),
        ),
        SizedBox(height: 10),
        Text(
          'Tap button to ${isLocked ? 'unlock' : 'lock'}',
          style: TextStyle(
            fontSize: 14,
            color: Colors.grey[600],
          ),
        ),
      ],
    );
  }
}
```

### 2. Control Buttons Widget

```dart
class LockControlButtons extends StatelessWidget {
  final bool isLocked;
  final bool isLoading;
  final VoidCallback onLock;
  final VoidCallback onUnlock;

  const LockControlButtons({
    required this.isLocked,
    required this.isLoading,
    required this.onLock,
    required this.onUnlock,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.symmetric(horizontal: 20),
      child: Row(
        children: [
          Expanded(
            child: _buildButton(
              onPressed: isLoading || isLocked ? null : onLock,
              icon: Icons.lock,
              label: 'LOCK',
              backgroundColor: Colors.red,
              isLoading: isLoading && isLocked,
            ),
          ),
          SizedBox(width: 20),
          Expanded(
            child: _buildButton(
              onPressed: isLoading || !isLocked ? null : onUnlock,
              icon: Icons.lock_open,
              label: 'UNLOCK',
              backgroundColor: Colors.green,
              isLoading: isLoading && !isLocked,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildButton({
    required VoidCallback? onPressed,
    required IconData icon,
    required String label,
    required Color backgroundColor,
    required bool isLoading,
  }) {
    return ElevatedButton.icon(
      onPressed: onPressed,
      icon: isLoading
          ? SizedBox(
              width: 24,
              height: 24,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                valueColor: AlwaysStoppedAnimation(Colors.white),
              ),
            )
          : Icon(icon),
      label: Text(label),
      style: ElevatedButton.styleFrom(
        backgroundColor: backgroundColor,
        disabledBackgroundColor: Colors.grey[300],
        foregroundColor: Colors.white,
        disabledForegroundColor: Colors.grey,
        padding: EdgeInsets.symmetric(vertical: 15),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
        ),
        elevation: onPressed != null ? 4 : 0,
      ),
    );
  }
}
```

### 3. Status Bar Widget

```dart
class LockStatusBar extends StatelessWidget {
  final bool isLocked;
  final DateTime? lastChanged;
  final String? changedBy;

  const LockStatusBar({
    required this.isLocked,
    this.lastChanged,
    this.changedBy,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: EdgeInsets.all(16),
      padding: EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.grey[100],
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.grey[300]!),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                isLocked ? Icons.lock : Icons.lock_open,
                color: isLocked ? Colors.red : Colors.green,
              ),
              SizedBox(width: 12),
              Text(
                'Status: ${isLocked ? 'Locked' : 'Unlocked'}',
                style: TextStyle(
                  fontWeight: FontWeight.w600,
                  fontSize: 14,
                ),
              ),
            ],
          ),
          if (lastChanged != null) ...[
            SizedBox(height: 8),
            Text(
              'Last changed: ${_formatTime(lastChanged!)}',
              style: TextStyle(
                fontSize: 12,
                color: Colors.grey[600],
              ),
            ),
          ],
          if (changedBy != null) ...[
            SizedBox(height: 4),
            Text(
              'By: $changedBy',
              style: TextStyle(
                fontSize: 12,
                color: Colors.grey[600],
              ),
            ),
          ],
        ],
      ),
    );
  }

  String _formatTime(DateTime dateTime) {
    return '${dateTime.day}/${dateTime.month}/${dateTime.year} ${dateTime.hour}:${dateTime.minute.toString().padLeft(2, '0')}';
  }
}
```

## Full Screen Implementation

```dart
import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

class LockUnlockScreenDesign extends StatefulWidget {
  const LockUnlockScreenDesign({Key? key}) : super(key: key);

  @override
  State<LockUnlockScreenDesign> createState() => _LockUnlockScreenDesignState();
}

class _LockUnlockScreenDesignState extends State<LockUnlockScreenDesign> {
  late ScrollController _scrollController;

  @override
  void initState() {
    super.initState();
    _scrollController = ScrollController();
    _refreshStatus();
  }

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  void _refreshStatus() {
    context.read<ManagerBloc>().add(const StatusRefreshRequested());
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: _buildAppBar(),
      body: BlocBuilder<ManagerBloc, ManagerState>(
        builder: (context, state) {
          if (state is LockStateChanged) {
            return _buildLockedContent(state.lockState);
          } else if (state is ManagerLoading) {
            return _buildLoadingContent();
          } else if (state is ManagerError) {
            return _buildErrorContent(state.message);
          }
          return _buildInitialContent();
        },
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: _refreshStatus,
        child: Icon(Icons.refresh),
      ),
    );
  }

  PreferredSizeWidget _buildAppBar() {
    return AppBar(
      elevation: 0,
      backgroundColor: Colors.white,
      foregroundColor: Colors.black,
      title: Text(
        'Door Lock',
        style: TextStyle(
          fontSize: 20,
          fontWeight: FontWeight.w600,
        ),
      ),
      centerTitle: true,
      actions: [
        IconButton(
          icon: Icon(Icons.settings),
          onPressed: () {
            // Navigate to settings
          },
        ),
      ],
    );
  }

  Widget _buildLockedContent(LockState lockState) {
    return SingleChildScrollView(
      controller: _scrollController,
      child: Padding(
        padding: EdgeInsets.symmetric(vertical: 40),
        child: Column(
          children: [
            // Status Display
            LockStatusDisplay(
              isLocked: lockState.isLocked,
              onRefresh: _refreshStatus,
            ),
            SizedBox(height: 60),
            // Control Buttons
            LockControlButtons(
              isLocked: lockState.isLocked,
              isLoading: false,
              onLock: () {
                context.read<ManagerBloc>().add(const LockRequested());
              },
              onUnlock: () {
                context.read<ManagerBloc>().add(const UnlockRequested());
              },
            ),
            SizedBox(height: 30),
            // Status Bar
            LockStatusBar(
              isLocked: lockState.isLocked,
              lastChanged: lockState.lastChanged,
              changedBy: lockState.changedBy,
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildLoadingContent() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          SizedBox(
            width: 80,
            height: 80,
            child: CircularProgressIndicator(strokeWidth: 4),
          ),
          SizedBox(height: 20),
          Text('Processing your request...'),
        ],
      ),
    );
  }

  Widget _buildErrorContent(String message) {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.error_outline, size: 80, color: Colors.red),
          SizedBox(height: 20),
          Text(
            'Error',
            style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
          ),
          SizedBox(height: 10),
          Padding(
            padding: EdgeInsets.symmetric(horizontal: 20),
            child: Text(
              message,
              textAlign: TextAlign.center,
              style: TextStyle(color: Colors.grey[600]),
            ),
          ),
          SizedBox(height: 20),
          ElevatedButton(
            onPressed: _refreshStatus,
            child: Text('Retry'),
          ),
        ],
      ),
    );
  }

  Widget _buildInitialContent() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          SizedBox(
            width: 80,
            height: 80,
            child: CircularProgressIndicator(strokeWidth: 4),
          ),
          SizedBox(height: 20),
          Text('Loading lock status...'),
        ],
      ),
    );
  }
}
```

## Alternative Designs

### Design Option 1: Card-Based

```dart
// Shows lock status as large card
Card(
  elevation: 8,
  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
  child: Container(
    width: 280,
    height: 300,
    decoration: BoxDecoration(
      gradient: LinearGradient(
        colors: isLocked
            ? [Colors.red.shade400, Colors.red.shade600]
            : [Colors.green.shade400, Colors.green.shade600],
      ),
      borderRadius: BorderRadius.circular(20),
    ),
    child: Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Icon(
          isLocked ? Icons.lock : Icons.lock_open,
          size: 100,
          color: Colors.white,
        ),
        SizedBox(height: 20),
        Text(
          isLocked ? 'LOCKED' : 'UNLOCKED',
          style: TextStyle(
            color: Colors.white,
            fontSize: 28,
            fontWeight: FontWeight.bold,
          ),
        ),
      ],
    ),
  ),
)
```

### Design Option 2: Animated Slider

```dart
// Swipe to unlock/lock
Padding(
  padding: EdgeInsets.all(20),
  child: GestureDetector(
    onHorizontalDragEnd: (details) {
      if (details.primaryVelocity! > 0) {
        // Swipe right to unlock
        context.read<ManagerBloc>().add(const UnlockRequested());
      } else {
        // Swipe left to lock
        context.read<ManagerBloc>().add(const LockRequested());
      }
    },
    child: Container(
      height: 60,
      decoration: BoxDecoration(
        color: Colors.grey[200],
        borderRadius: BorderRadius.circular(30),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Padding(
            padding: EdgeInsets.all(8),
            child: Text('← Swipe to unlock →'),
          ),
        ],
      ),
    ),
  ),
)
```

## Responsive Design

```dart
class ResponsiveLockScreen extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final screenSize = MediaQuery.of(context).size;
    final isMobile = screenSize.width < 600;

    if (isMobile) {
      return _buildMobileLayout();
    } else {
      return _buildTabletLayout();
    }
  }

  Widget _buildMobileLayout() {
    // Single column layout
    return Column(
      children: [
        LockStatusDisplay(...),
        SizedBox(height: 40),
        LockControlButtons(...),
      ],
    );
  }

  Widget _buildTabletLayout() {
    // Two column layout
    return Row(
      children: [
        Expanded(child: LockStatusDisplay(...)),
        Expanded(child: LockControlButtons(...)),
      ],
    );
  }
}
```

## Animation Examples

### Pulse Animation
```dart
class PulseAnimation extends StatefulWidget {
  @override
  State<PulseAnimation> createState() => _PulseAnimationState();
}

class _PulseAnimationState extends State<PulseAnimation>
    with TickerProviderStateMixin {
  late AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      duration: Duration(seconds: 1),
      vsync: this,
    )..repeat();
  }

  @override
  Widget build(BuildContext context) {
    return ScaleTransition(
      scale: Tween<double>(begin: 1.0, end: 1.1).animate(_controller),
      child: Container(
        width: 200,
        height: 200,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: Colors.red.shade100,
        ),
        child: Icon(Icons.lock, size: 80),
      ),
    );
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }
}
```

## Theme Configuration

```dart
class AppTheme {
  static ThemeData lightTheme = ThemeData(
    useMaterial3: true,
    brightness: Brightness.light,
    colorScheme: ColorScheme.fromSeed(
      seedColor: Colors.blue,
    ),
    appBarTheme: AppBarTheme(
      elevation: 0,
      backgroundColor: Colors.white,
      foregroundColor: Colors.black,
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        padding: EdgeInsets.symmetric(horizontal: 30, vertical: 15),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
        ),
      ),
    ),
  );

  static ThemeData darkTheme = ThemeData(
    useMaterial3: true,
    brightness: Brightness.dark,
    colorScheme: ColorScheme.fromSeed(
      seedColor: Colors.blue,
      brightness: Brightness.dark,
    ),
  );
}
```

## Implementation Tips

1. **Performance:**
   - Use const constructors
   - Avoid rebuilds with BlocListener
   - Cache images/animations

2. **Accessibility:**
   - Add semantic labels
   - Use sufficient contrast
   - Support text scaling

3. **User Experience:**
   - Add haptic feedback
   - Show loading states
   - Provide error messages
   - Use animations judiciously

4. **Testing:**
   - Test all button states
   - Test error scenarios
   - Test responsive layouts
   - Test animations