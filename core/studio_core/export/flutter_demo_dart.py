"""Dart sources of the Flame demo project written by export/flutter_demo.py (identical for every export; the scene
itself comes from assets/demo/demo.json)."""
from __future__ import annotations

PACKAGE = "asset_studio_demo"

PUBSPEC = """name: asset_studio_demo
description: Flame demo of the exported assets (8-direction player, tile map, parallax, props, lighting).
publish_to: none
version: 1.0.0+1

environment:
  sdk: ^3.7.0

dependencies:
  flutter:
    sdk: flutter
  flame: ^1.38.2
  flame_tiled: ^3.1.2

dev_dependencies:
  flutter_test:
    sdk: flutter
  flutter_lints: ^5.0.0

"""

ANALYSIS = """include: package:flutter_lints/flutter.yaml

analyzer:
  exclude:
    - build/**
    - windows/**
    - android/**
    - ios/**
    - linux/**
    - macos/**
    - web/**
"""

MAIN = r"""import 'package:flame/game.dart';
import 'package:flutter/material.dart';

import 'game.dart';

void main() {
  runApp(
    MaterialApp(
      debugShowCheckedModeBanner: false,
      home: Scaffold(
        backgroundColor: const Color(0xFF1B1F2A),
        body: GameWidget<DemoGame>.controlled(
          gameFactory: DemoGame.new,
          errorBuilder: (context, error) => Center(
            child: Text('$error', style: const TextStyle(color: Colors.white)),
          ),
        ),
      ),
    ),
  );
}
"""

CONFIG = r"""import 'dart:convert';

import 'package:flame/components.dart';
import 'package:flutter/services.dart' show rootBundle;

double _d(Object? v) => (v! as num).toDouble();

class ClipInfo {
  ClipInfo(Map<String, dynamic> j)
      : name = j['name'] as String,
        state = j['state'] as String,
        dir = j['dir'] as String;

  final String name;
  final String state;
  final String dir;
}

class SheetInfo {
  SheetInfo(Map<String, dynamic> j)
      : name = j['name'] as String,
        frame = Vector2(_d((j['frame'] as List)[0]), _d((j['frame'] as List)[1])),
        anchor = Anchor(_d((j['anchor'] as List)[0]), _d((j['anchor'] as List)[1])),
        height = _d(j['height']),
        normal = j['normal'] as bool,
        clips = [for (final c in j['clips'] as List) ClipInfo(c as Map<String, dynamic>)];

  final String name;
  final Vector2 frame;
  final Anchor anchor;

  /// Visible height (alpha bounding box) of the first frame, in sheet pixels.
  final double height;
  final bool normal;
  final List<ClipInfo> clips;
}

class PropInfo {
  PropInfo(Map<String, dynamic> j)
      : sprite = j['sprite'] as String,
        x = _d(j['x']),
        y = _d(j['y']),
        w = _d(j['w']),
        h = _d(j['h']);

  final String sprite;
  final double x;
  final double y;
  final double w;
  final double h;
}

class DemoConfig {
  DemoConfig(Map<String, dynamic> j)
      : title = j['title'] as String,
        sideview = j['style'] == 'sideview',
        tile = _d(j['tile']),
        cols = j['cols'] as int,
        rows = j['rows'] as int,
        tmx = j['tmx'] as String?,
        ground = j['ground'] as String?,
        solid = [for (final c in j['solid'] as List) (c as List).cast<int>()],
        spawn = Vector2(_d((j['spawn'] as List)[0]), _d((j['spawn'] as List)[1])),
        speed = _d(j['speed']),
        gravity = _d(j['gravity']),
        jump = _d(j['jump']),
        charHeight = _d(j['charHeight']),
        viewRows = _d(j['viewRows']),
        parallax = j['parallax'] as String?,
        lit = j['lit'] as bool,
        sheets = [for (final s in j['sheets'] as List) SheetInfo(s as Map<String, dynamic>)],
        props = [for (final p in j['props'] as List) PropInfo(p as Map<String, dynamic>)];

  static Future<DemoConfig> load() async => DemoConfig(
        jsonDecode(await rootBundle.loadString('assets/demo/demo.json')) as Map<String, dynamic>,
      );

  final String title;
  final bool sideview;
  final double tile;
  final int cols;
  final int rows;
  final String? tmx;

  /// Tileset image (relative to assets/) repeated as the floor when there is no .tmx.
  final String? ground;
  final List<List<int>> solid;
  final Vector2 spawn;
  final double speed;
  final double gravity;
  final double jump;
  final double charHeight;
  final double viewRows;
  final String? parallax;
  final bool lit;
  final List<SheetInfo> sheets;
  final List<PropInfo> props;

  double get mapWidth => cols * tile;
  double get mapHeight => rows * tile;
}
"""

GAME = r"""import 'dart:math' as math;
import 'dart:ui' show Color, Paint;

import 'package:flame/components.dart';
import 'package:flame/game.dart';
import 'package:flame/input.dart';
import 'package:flame/text.dart';
import 'package:flutter/foundation.dart' show defaultTargetPlatform, TargetPlatform, kIsWeb;
import 'package:flutter/painting.dart' show EdgeInsets, Shadow, TextStyle;
import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart' show KeyEventResult;

import 'demo_config.dart';
import 'player.dart';
import 'world.dart';

class DemoGame extends FlameGame with KeyboardEvents {
  DemoGame({bool? showJoystick}) : _showJoystick = showJoystick;

  final bool? _showJoystick;
  final Set<LogicalKeyboardKey> _keys = {};
  late final DemoConfig config;
  late final DemoMap map;
  late final Player player;
  late final TextComponent _label;
  JoystickComponent? _joystick;
  ParallaxComponent? _parallax;
  bool loadedAll = false;

  @override
  Color backgroundColor() => const Color(0xFF1B1F2A);

  @override
  Future<void> onLoad() async {
    config = await DemoConfig.load();
    map = DemoMap();
    player = Player();
    await world.addAll([map, player]);
    await map.loaded;
    player.position = config.spawn.clone();
    if (config.parallax != null) {
      _parallax = await map.buildParallax();
      await camera.backdrop.add(_parallax!);
    }
    await _addHud();
    loadedAll = true;
    onGameResize(size);
    _follow(1);
  }

  Future<void> _addHud() async {
    final style = TextPaint(
      style: const TextStyle(color: Color(0xFFFFFFFF), fontSize: 14, shadows: [Shadow(blurRadius: 2)]),
    );
    _label = TextComponent(position: Vector2(10, 8), textRenderer: style);
    final fps = FpsTextComponent(position: Vector2(10, 28), textRenderer: style);
    await camera.viewport.addAll([_label, fps]);
    final touch = !kIsWeb && (defaultTargetPlatform == TargetPlatform.android || defaultTargetPlatform == TargetPlatform.iOS);
    if (_showJoystick ?? touch) {
      final paint = Paint()..color = const Color(0x66FFFFFF);
      _joystick = JoystickComponent(
        knob: CircleComponent(radius: 22, paint: paint),
        background: CircleComponent(radius: 56, paint: Paint()..color = const Color(0x33FFFFFF)),
        margin: const EdgeInsets.only(left: 40, bottom: 40),
      );
      final attack = HudButtonComponent(
        button: CircleComponent(radius: 32, paint: paint),
        buttonDown: CircleComponent(radius: 32, paint: Paint()..color = const Color(0xAAFFFFFF)),
        margin: const EdgeInsets.only(right: 40, bottom: 40),
        onPressed: player.attack,
      );
      await camera.viewport.addAll([_joystick!, attack]);
    }
  }

  @override
  void onGameResize(Vector2 size) {
    super.onGameResize(size);
    if (!loadedAll) {
      return;
    }
    final c = config;
    // Whole-pixel tile size + pixel-snapped camera: a fractional tile edge samples the neighbouring atlas tile (seams).
    final fit = math.min(size.y / (c.viewRows * c.tile), size.x / (10 * c.tile));
    camera.viewfinder.zoom = fit >= 1 ? (fit * c.tile).floorToDouble() / c.tile : fit;
  }

  @override
  void update(double dt) {
    super.update(dt);
    if (loadedAll) {
      _label.text = '${config.title}  |  ${player.label}';
      _parallax?.parallax?.baseVelocity.setValues(player.velocity.x, 0);
      _follow(dt);
    }
  }

  /// Centres on the player, staying inside the map (a map smaller than the view is centred).
  void _follow(double dt) {
    final half = size / (2 * camera.viewfinder.zoom);
    double axis(double p, double h, double extent) => h * 2 >= extent ? extent / 2 : p.clamp(h, extent - h);
    final target = Vector2(
      axis(player.position.x, half.x, config.mapWidth),
      axis(player.position.y - config.charHeight * 0.4, half.y, config.mapHeight),
    );
    final pos = camera.viewfinder.position;
    final next = pos + (target - pos) * math.min(1.0, dt * 10);
    final z = camera.viewfinder.zoom;
    final half2 = size / 2;
    camera.viewfinder.position = Vector2(
      ((next.x * z - half2.x).roundToDouble() + half2.x) / z,
      ((next.y * z - half2.y).roundToDouble() + half2.y) / z,
    );
  }

  /// Movement intent in [-1, 1]; the joystick wins when it is held.
  Vector2 get input {
    final j = _joystick;
    if (j != null && j.direction != JoystickDirection.idle) {
      return j.relativeDelta.clone();
    }
    double axis(LogicalKeyboardKey neg, LogicalKeyboardKey alt, LogicalKeyboardKey pos, LogicalKeyboardKey alt2) =>
        ((_keys.contains(pos) || _keys.contains(alt2)) ? 1.0 : 0.0) -
        ((_keys.contains(neg) || _keys.contains(alt)) ? 1.0 : 0.0);
    final v = Vector2(
      axis(LogicalKeyboardKey.arrowLeft, LogicalKeyboardKey.keyA, LogicalKeyboardKey.arrowRight, LogicalKeyboardKey.keyD),
      axis(LogicalKeyboardKey.arrowUp, LogicalKeyboardKey.keyW, LogicalKeyboardKey.arrowDown, LogicalKeyboardKey.keyS),
    );
    return v.length2 > 1 ? v.normalized() : v;
  }

  @override
  KeyEventResult onKeyEvent(KeyEvent event, Set<LogicalKeyboardKey> keysPressed) {
    _keys
      ..clear()
      ..addAll(keysPressed);
    if (event is KeyDownEvent) {
      final k = event.logicalKey;
      if (k == LogicalKeyboardKey.keyN) {
        player.normalizeScale = !player.normalizeScale;
      } else if (k == LogicalKeyboardKey.keyL) {
        player.toggleLight();
      } else if (k == LogicalKeyboardKey.keyJ || k == LogicalKeyboardKey.keyK || k == LogicalKeyboardKey.enter) {
        player.attack();
      } else if (k == LogicalKeyboardKey.space) {
        config.sideview ? player.jump() : player.attack();
      } else if (config.sideview && (k == LogicalKeyboardKey.arrowUp || k == LogicalKeyboardKey.keyW)) {
        player.jump();
      }
    }
    return KeyEventResult.handled;
  }
}
"""

PLAYER = r"""import 'dart:math' as math;
import 'dart:ui';

import 'package:flame/components.dart';

import 'demo_config.dart';
import 'game.dart';
import 'gen/asset_studio_flame.dart';
import 'gen/asset_studio_shaders.dart';

const _dirs = ['E', 'SE', 'S', 'SW', 'W', 'NW', 'N', 'NE'];
const _mirror = {'E': 'W', 'W': 'E', 'NE': 'NW', 'NW': 'NE', 'SE': 'SW', 'SW': 'SE'};

class _Pick {
  _Pick(this.anim, this.sheet, this.dir);

  final SpriteAnimation anim;
  final SheetInfo sheet;
  final String dir;
}

/// 8-direction character: clips come from every sheet of the export (state_DIR), a missing direction falls back to
/// the mirrored one, then to any direction of the state (flipped to face the walking side).
class Player extends PositionComponent with HasGameReference<DemoGame> {
  Player() : super(anchor: Anchor.center);

  final Map<String, _Pick> _clips = {};
  final Map<String, LitSpriteEffect> _effects = {};
  late final ShaderSpriteAnimationComponent _body;
  late final double _rawScale;
  late final double _footW;
  late final double _footH;
  final Vector2 velocity = Vector2.zero();
  LightSource? light;
  bool normalizeScale = true;
  String _dir = 'S';
  String _applied = '';
  String _clipLabel = '';
  double _scale = 1;
  double _attackLeft = 0;
  double _vy = 0;
  bool _grounded = false;

  String get label => '$_clipLabel  x${_scale.toStringAsFixed(2)}${normalizeScale ? '' : ' raw'}  '
      '${(game.config.charHeight / game.config.tile).toStringAsFixed(1)} tile';

  @override
  Future<void> onLoad() async {
    final c = game.config;
    for (final sheet in c.sheets) {
      final loaded = await loadAsAnimations(game.images, sheet.name);
      for (final clip in sheet.clips) {
        final anim = loaded[clip.name];
        if (anim == null) {
          continue;
        }
        final key = '${clip.state}/${clip.dir}';
        final old = _clips[key];
        if (old == null || anim.frames.length > old.anim.frames.length) {
          _clips[key] = _Pick(anim, sheet, clip.dir);
        }
      }
      if (c.lit && sheet.normal) {
        final normal = await loadSheetNormal(game.images, sheet.name);
        try {
          _effects[sheet.name] = await LitSpriteEffect.load(normalMap: normal);
        } catch (_) {
          // Shader programs are unavailable on some test / software renderers; the sprite is then drawn unlit.
        }
      }
    }
    final main = _clips['idle/S']?.sheet ?? c.sheets.first;
    _rawScale = c.charHeight / main.height;
    _footW = c.tile * (c.sideview ? 0.5 : 0.6);
    _footH = c.sideview ? c.charHeight * 0.9 : c.tile * 0.4;
    if (_effects.isNotEmpty) {
      final lamp = LightSource(radius: c.tile * 5, elevation: c.tile * 1.2);
      for (final e in _effects.values) {
        e.lights = [lamp];
      }
      light = lamp;
      await game.world.add(lamp);
    }
    _body = ShaderSpriteAnimationComponent();
    await add(_body);
    _apply('idle', _dir);
  }

  Rect footAt(double x, double y) => Rect.fromLTRB(x - _footW / 2, y - _footH, x + _footW / 2, y);

  void attack() {
    final p = _find('attack', _dir);
    if (p != null && _attackLeft <= 0) {
      _attackLeft = p.$1.anim.frames.fold(0.0, (t, f) => t + f.stepTime);
      _applied = '';
    }
  }

  void toggleLight() => light?.intensity = light!.intensity > 0 ? 0 : 1;

  void jump() {
    if (_grounded) {
      _vy = -game.config.jump;
      _grounded = false;
    }
  }

  (_Pick, bool)? _find(String state, String dir) {
    final exact = _clips['$state/$dir'];
    if (exact != null) {
      return (exact, false);
    }
    final mirrored = _clips['$state/${_mirror[dir]}'];
    if (mirrored != null) {
      return (mirrored, true);
    }
    final any = _clips.entries.where((e) => e.key.startsWith('$state/')).map((e) => e.value).toList();
    if (any.isEmpty) {
      return null;
    }
    final side = any.firstWhere((p) => p.dir.contains('E') || p.dir.contains('W'), orElse: () => any.first);
    final facingLeft = dir.contains('W');
    final clipLeft = side.dir.contains('W');
    final horizontal = side.dir.contains('E') || side.dir.contains('W');
    return (side, horizontal && facingLeft != clipLeft);
  }

  void _apply(String state, String dir) {
    final found = _find(state, dir) ?? _find('idle', dir) ?? (_clips.values.first, false);
    final p = found.$1;
    final sheet = p.sheet;
    _scale = normalizeScale ? game.config.charHeight / sheet.height : _rawScale;
    _body
      ..animation = p.anim
      ..size = sheet.frame
      ..anchor = sheet.anchor
      ..scale = Vector2(found.$2 ? -_scale : _scale, _scale)
      ..effect = _effects[sheet.name];
    _clipLabel = '$state/$dir  ${sheet.frame.x.round()}px';
    _applied = '$state/$dir/$normalizeScale';
  }

  @override
  void update(double dt) {
    super.update(dt);
    final c = game.config;
    final v = game.input;
    _attackLeft = math.max(0, _attackLeft - dt);
    final moving = _attackLeft <= 0 && (c.sideview ? v.x.abs() > 0.05 : v.length2 > 0.0025);
    if (v.length2 > 0.0025 && _attackLeft <= 0) {
      _dir = c.sideview ? (v.x < 0 ? 'W' : 'E') : _dirs[((math.atan2(v.y, v.x) / (math.pi / 4)).round()) & 7];
    }
    if (_attackLeft <= 0 || moving) {
      _move(c.sideview ? Vector2(v.x * c.speed * dt, 0) : v * (c.speed * dt), dt);
    } else if (c.sideview) {
      _move(Vector2.zero(), dt);
    }
    final state = _attackLeft > 0 ? 'attack' : (moving ? 'walk' : 'idle');
    if (_applied != '$state/$_dir/$normalizeScale') {
      _apply(state, _dir);
    }
    priority = position.y.round();
    light?.position.setValues(position.x, position.y - game.config.charHeight * 0.5);
  }

  void _move(Vector2 d, double dt) {
    final c = game.config;
    final map = game.map;
    var x = position.x + d.x;
    var y = position.y + d.y;
    if (map.blocked(footAt(x, position.y))) {
      x = position.x;
    }
    if (c.sideview) {
      _vy += c.gravity * dt;
      y = position.y + _vy * dt;
    }
    if (map.blocked(footAt(x, y))) {
      if (c.sideview) {
        _grounded = _vy > 0;
        _vy = 0;
      }
      y = position.y;
    } else if (c.sideview) {
      _grounded = false;
    }
    final nx = x.clamp(_footW / 2, c.mapWidth - _footW / 2);
    final ny = y.clamp(_footH, c.mapHeight);
    if (dt > 0) {
      velocity.setValues((nx - position.x) / dt, (ny - position.y) / dt);
    }
    position.setValues(nx, ny);
  }
}
"""

WORLD = r"""import 'dart:ui';

import 'package:flame/cache.dart';
import 'package:flame/components.dart';
import 'game.dart';
import 'gen/asset_studio_flame.dart';

/// Floor used when the export has a tileset but no .tmx: tile 0 of the atlas repeated over the map.
class _Floor extends PositionComponent {
  _Floor(this.image, this.tile, Vector2 mapSize) : super(size: mapSize, priority: -1000);

  final Image image;
  final double tile;
  final Paint _paint = Paint()..filterQuality = FilterQuality.none;

  @override
  void render(Canvas canvas) {
    final src = Rect.fromLTWH(0, 0, tile, tile);
    for (var y = 0.0; y < size.y; y += tile) {
      for (var x = 0.0; x < size.x; x += tile) {
        canvas.drawImageRect(image, src, Rect.fromLTWH(x, y, tile, tile), _paint);
      }
    }
  }
}

/// Plain grid so a scene without any tileset is still readable.
class _Grid extends PositionComponent {
  _Grid(this.tile, Vector2 mapSize) : super(size: mapSize, priority: -1000);

  final double tile;
  final Paint _fill = Paint()..color = const Color(0xFF2C3446);
  final Paint _line = Paint()
    ..color = const Color(0xFF3A4458)
    ..strokeWidth = 1;

  @override
  void render(Canvas canvas) {
    canvas.drawRect(size.toRect(), _fill);
    for (var x = 0.0; x <= size.x; x += tile) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.y), _line);
    }
    for (var y = 0.0; y <= size.y; y += tile) {
      canvas.drawLine(Offset(0, y), Offset(size.x, y), _line);
    }
  }
}

/// Ground, props and the solid-tile / prop-footprint colliders.
class DemoMap extends Component with HasGameReference<DemoGame> {
  final List<Rect> _blockers = [];
  final Images _assets = Images(prefix: 'assets/');

  bool blocked(Rect r) => _blockers.any(r.overlaps);

  @override
  Future<void> onLoad() async {
    final c = game.config;
    final mapSize = Vector2(c.mapWidth, c.mapHeight);
    if (c.tmx != null) {
      final tiled = await loadTiledMap(c.tmx!, Vector2.all(c.tile));
      tiled.priority = -1000;
      await add(tiled);
    } else if (c.ground != null) {
      await add(_Floor(await _assets.load(c.ground!), c.tile, mapSize));
    } else {
      await add(_Grid(c.tile, mapSize));
    }
    for (final cell in c.solid) {
      _blockers.add(Rect.fromLTWH(cell[0] * c.tile, cell[1] * c.tile, c.tile, c.tile));
    }
    for (final p in c.props) {
      final sprite = Sprite(await _assets.load(p.sprite));
      await game.world.add(
        SpriteComponent(
          sprite: sprite,
          size: Vector2(p.w, p.h),
          position: Vector2(p.x, p.y),
          anchor: Anchor.bottomCenter,
          priority: p.y.round(),
        ),
      );
      final fw = p.w * 0.6;
      final fh = c.sideview ? p.h : c.tile * 0.4;
      _blockers.add(Rect.fromLTWH(p.x - fw / 2, p.y - fh, fw, fh));
    }
  }

  Future<ParallaxComponent> buildParallax() async {
    final p = await loadParallax(game.images, game.config.parallax!);
    p.parallax!.baseVelocity = Vector2.zero();
    return p;
  }
}
"""

TEST = r"""import 'dart:io';
import 'dart:math' as math;

import 'package:asset_studio_demo/game.dart';
import 'package:flame/game.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

const _golden = 'goldens/first_frame.png';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('demo boots, renders and the player walks', (tester) async {
    tester.view
      ..physicalSize = const Size(960, 540)
      ..devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final game = DemoGame(showJoystick: false);
    await tester.pumpWidget(
      MaterialApp(home: Scaffold(body: GameWidget<DemoGame>(game: game))),
    );
    for (var i = 0; i < 200 && !game.loadedAll; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 50)));
      await tester.pump(const Duration(milliseconds: 16));
    }
    expect(game.loadedAll, isTrue);
    await tester.pump(const Duration(milliseconds: 100));
    await tester.pump(const Duration(milliseconds: 100));

    if (File('test/$_golden').existsSync()) {
      await expectLater(find.byType(GameWidget<DemoGame>), matchesGoldenFile(_golden));
    }

    final start = game.player.position.clone();
    var farthest = 0.0;
    for (final key in [LogicalKeyboardKey.arrowRight, LogicalKeyboardKey.arrowUp, LogicalKeyboardKey.arrowLeft]) {
      await tester.sendKeyDownEvent(key);
      for (var i = 0; i < 30; i++) {
        await tester.pump(const Duration(milliseconds: 33));
        farthest = math.max(farthest, game.player.position.distanceTo(start));
      }
      await tester.sendKeyUpEvent(key);
    }
    expect(farthest, greaterThan(game.config.tile * 0.5));
  });
}
"""

README = """{title} — Flame demo from AI Asset Studio.

    flutter pub get
    flutter run -d windows        (or any device; add the platform first with `flutter create --platforms=windows .`)

Controls: arrow keys / WASD move (8 directions), Space or J attack (side-view: Space / Up jump), N toggles per-clip
scale normalisation (off shows the raw proportions between sheets), L toggles the light. Touch: joystick + attack button.
The HUD shows the asset title, the clip on screen, its scale factor and FPS.
{notes}
`flutter test` boots the game, walks a few frames and compares the first frame with test/goldens/first_frame.png when
that file exists (create it with `flutter test --update-goldens`).
"""
