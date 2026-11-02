"""Dart runtime shared by every Flutter export that needs it: identical text for every asset, so a pack merge keeps one
copy. Raw strings: Dart's `$` and backslashes pass through untouched."""
from __future__ import annotations

from studio_core.export.flutter_dart import GEN_HEAD

TILES_FILE = "lib/gen/asset_studio_tiles.dart"
VFX_FILE = "lib/gen/asset_studio_vfx.dart"
WIDGETS_FILE = "lib/gen/asset_studio_widgets.dart"

TILES_DART = GEN_HEAD + r"""import 'dart:convert';
import 'dart:ui' as ui;

import 'package:flame/cache.dart';
import 'package:flame/components.dart';
import 'package:flutter/services.dart' show rootBundle;

Future<Map<String, dynamic>> _readJson(String path) async =>
    jsonDecode(await rootBundle.loadString(path)) as Map<String, dynamic>;

/// Frames of `assets/tiles/<name>_tile_anim.png` for the tile at atlas cell ([col], [row]) (a single tile sits at 0, 0).
Future<SpriteAnimation> loadTileAnimation(
  String name, {
  int col = 0,
  int row = 0,
}) async {
  final doc = await _readJson('assets/tiles/${name}_tile_anim.json');
  final image = await Images(prefix: 'assets/tiles/')
      .load('${name}_tile_anim.png');
  final size = (doc['tile_size'] as num).toDouble();
  final offset =
      ((doc['layout'] as Map<String, dynamic>)['frame_offset'] as List<dynamic>)
          .cast<num>();
  final frames = (doc['frames'] as num).toInt();
  return SpriteAnimation.spriteList([
    for (var k = 0; k < frames; k++)
      Sprite(
        image,
        srcPosition: Vector2(
          (col + k * offset[0]) * size,
          (row + k * offset[1]) * size,
        ),
        srcSize: Vector2.all(size),
      ),
  ], stepTime: 1 / (doc['fps'] as num).toDouble());
}

/// Blob-47 terrain set: picks the tile whose neighbour mask matches, like the Godot / Unity autotile exports.
class AutotileSet {
  AutotileSet._(this.tileSize, this._byMask, this._fill);

  final double tileSize;
  final Map<int, Sprite> _byMask;
  final Sprite _fill;

  static const _dirs = [
    (0, -1),
    (1, -1),
    (1, 0),
    (1, 1),
    (0, 1),
    (-1, 1),
    (-1, 0),
    (-1, -1),
  ];
  static const _corners = [(1, 0, 2), (3, 4, 2), (5, 4, 6), (7, 0, 6)];

  /// Loads `assets/tiles/<name>_autotile.png` + `.json`.
  static Future<AutotileSet> load(String name) async {
    final doc = await _readJson('assets/tiles/${name}_autotile.json');
    final image = await Images(prefix: 'assets/tiles/')
        .load('${name}_autotile.png');
    final size = (doc['tile_size'] as num).toDouble();
    final byMask = <int, Sprite>{};
    Sprite? fill;
    for (final raw in doc['tiles'] as List<dynamic>) {
      final tile = raw as Map<String, dynamic>;
      final sprite = Sprite(
        image,
        srcPosition: Vector2(
          (tile['col'] as num) * size,
          (tile['row'] as num) * size,
        ),
        srcSize: Vector2.all(size),
      );
      final mask = tile['mask'] as int?;
      if (mask == null) {
        fill = sprite;
      } else {
        byMask[mask] = sprite;
      }
    }
    return AutotileSet._(size, byMask, fill ?? byMask.values.first);
  }

  /// Neighbour mask of cell ([x], [y]) in [cells] (`cells[y][x]` is terrain A); off-map counts as B.
  static int maskAt(List<List<bool>> cells, int x, int y) {
    var mask = 0;
    for (var i = 0; i < _dirs.length; i++) {
      final nx = x + _dirs[i].$1;
      final ny = y + _dirs[i].$2;
      if (ny >= 0 && ny < cells.length && nx >= 0 && nx < cells[ny].length) {
        if (cells[ny][nx]) mask |= 1 << i;
      }
    }
    for (final (corner, a, b) in _corners) {
      if (mask & (1 << a) == 0 || mask & (1 << b) == 0) mask &= ~(1 << corner);
    }
    return mask;
  }

  Sprite spriteFor(int mask) => _byMask[mask] ?? _fill;

  /// Tile for cell ([x], [y]): the matching edge tile for A, the plain fill for B.
  Sprite spriteAt(List<List<bool>> cells, int x, int y) =>
      cells[y][x] ? spriteFor(maskAt(cells, x, y)) : _fill;
}

/// Draws a `cells[y][x]` grid with an [AutotileSet].
class AutotileComponent extends PositionComponent {
  AutotileComponent(this.tiles, this.cells, {super.position})
    : super(
        size: Vector2(
          (cells.isEmpty ? 0 : cells.first.length) * tiles.tileSize,
          cells.length * tiles.tileSize,
        ),
      );

  final AutotileSet tiles;
  final List<List<bool>> cells;

  @override
  void render(ui.Canvas canvas) {
    final size = Vector2.all(tiles.tileSize);
    for (var y = 0; y < cells.length; y++) {
      for (var x = 0; x < cells[y].length; x++) {
        tiles
            .spriteAt(cells, x, y)
            .render(
              canvas,
              position: Vector2(x * tiles.tileSize, y * tiles.tileSize),
              size: size,
            );
      }
    }
  }
}
"""

VFX_DART = GEN_HEAD + r"""import 'dart:convert';
import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flame/cache.dart';
import 'package:flame/components.dart';
import 'package:flutter/services.dart' show rootBundle;

const double _deg = math.pi / 180;

double _num(Object? v) => (v as num).toDouble();

List<double> _pair(Object? v) {
  final list = v as List<dynamic>;
  return [_num(list[0]), _num(list[1])];
}

int _byte(double v) => v < 0 ? 0 : (v > 255 ? 255 : v.round());

class VfxTexture {
  const VfxTexture(this.image, this.cols, this.rows);

  final ui.Image image;
  final int cols;
  final int rows;

  double get cellWidth => image.width / cols;
  double get cellHeight => image.height / rows;
}

/// One particle layer of a preset (units: [VfxLibrary.unit] pixels, y up in the file, y down on screen).
class VfxEmitter {
  VfxEmitter.fromJson(Map<String, dynamic> j)
    : name = j['name'] as String,
      texture = j['texture'] as String,
      additive = j['blend'] == 'add',
      delay = _num(j['delay']),
      burst = (j['burst'] as num).toInt(),
      rate = _num(j['rate']),
      life = _pair(j['lifetime']),
      speed = _pair(j['speed']),
      shape = j['shape'] as Map<String, dynamic>,
      gravity = _num(j['gravity']),
      drag = _num(j['drag']),
      size = _pair(j['size']),
      sizeCurve = [for (final p in j['size_curve'] as List<dynamic>) _pair(p)],
      rotation = _pair(j['rotation']),
      spin = _pair(j['spin']),
      velocityAligned = j['align'] == 'velocity',
      worldSpace = j['space'] == 'world',
      colors = [
        for (final p in j['colors'] as List<dynamic>)
          _stop(_num((p as List<dynamic>)[0]), p[1] as String),
      ],
      box = j['box'] == null ? null : (j['box'] as List<dynamic>).cast<num>(),
      direction = j['direction'] == null ? null : _pair(j['direction']),
      spread = j['spread'] == null ? 0 : _num(j['spread']);

  final String name;
  final String texture;
  final bool additive;
  final double delay;
  final int burst;
  final double rate;
  final List<double> life;
  final List<double> speed;
  final Map<String, dynamic> shape;
  final double gravity;
  final double drag;
  final List<double> size;
  final List<List<double>> sizeCurve;
  final List<double> rotation;
  final List<double> spin;
  final bool velocityAligned;
  final bool worldSpace;
  final List<List<double>> colors;
  final List<num>? box;
  final List<double>? direction;
  final double spread;

  static List<double> _stop(double t, String hex) {
    final h = hex.substring(1);
    int part(int i) => int.parse(h.substring(i, i + 2), radix: 16);
    return [
      t,
      part(0).toDouble(),
      part(2).toDouble(),
      part(4).toDouble(),
      h.length > 6 ? part(6).toDouble() : 255,
    ];
  }
}

class VfxPreset {
  VfxPreset.fromJson(Map<String, dynamic> j)
    : name = j['name'] as String,
      loop = j['loop'] as bool,
      duration = _num(j['duration']),
      emitters = [
        for (final e in j['emitters'] as List<dynamic>)
          VfxEmitter.fromJson(e as Map<String, dynamic>),
      ];

  final String name;
  final bool loop;
  final double duration;
  final List<VfxEmitter> emitters;

  /// Seconds until the last particle of a one-shot preset is gone.
  double get span => emitters.fold(
    0,
    (m, e) => math.max(m, e.delay + (e.burst > 0 ? 0 : duration) + e.life[1]),
  );
}

class _Particle {
  _Particle(
    this.x,
    this.y,
    this.vx,
    this.vy,
    this.life,
    this.rot,
    this.spin,
    this.size,
    this.frame,
  );

  double x;
  double y;
  double vx;
  double vy;
  double age = 0;
  final double life;
  double rot;
  final double spin;
  final double size;
  final int frame;
}

class _EmitterState {
  final particles = <_Particle>[];
  double carry = 0;
  double lastBurst = -1e9;
  bool burstDone = false;
}

/// Simulates and draws one preset. Add it to the game; one-shot presets remove themselves when they finish.
class VfxComponent extends PositionComponent {
  VfxComponent(
    this.preset,
    this.textures,
    this.unit, {
    this.autoRemove = true,
    int? seed,
    super.position,
    super.priority,
  }) : _random = math.Random(seed),
       _states = [for (final _ in preset.emitters) _EmitterState()];

  final VfxPreset preset;
  final Map<String, VfxTexture> textures;
  final double unit;
  final bool autoRemove;
  void Function()? onFinished;

  final math.Random _random;
  final List<_EmitterState> _states;
  final _alpha = ui.Paint()..filterQuality = ui.FilterQuality.low;
  final _add = ui.Paint()
    ..filterQuality = ui.FilterQuality.low
    ..blendMode = ui.BlendMode.plus;
  double _time = 0;
  bool _done = false;

  bool get isFinished => _done;

  /// Runs a looping preset forward so it starts full instead of empty.
  void prewarm([double seconds = 0]) {
    final total = seconds > 0
        ? seconds
        : preset.emitters.fold(0.0, (m, e) => math.max(m, e.life[1]));
    for (var t = 0.0; t < total; t += 1 / 30) {
      update(1 / 30);
    }
  }

  void play() {
    _time = 0;
    _done = false;
    for (final s in _states) {
      s.particles.clear();
      s.carry = 0;
      s.lastBurst = -1e9;
      s.burstDone = false;
    }
  }

  @override
  void update(double dt) {
    if (_done) return;
    _time += dt;
    for (var i = 0; i < _states.length; i++) {
      _step(preset.emitters[i], _states[i], dt);
    }
    if (!preset.loop &&
        _time > preset.span &&
        _states.every((s) => s.particles.isEmpty)) {
      _done = true;
      onFinished?.call();
      if (autoRemove) removeFromParent();
    }
  }

  double _between(List<double> range) =>
      range[0] + (range[1] - range[0]) * _random.nextDouble();

  void _step(VfxEmitter e, _EmitterState s, double dt) {
    final local = _time - e.delay;
    if (local >= 0) {
      if (e.burst > 0) {
        if (!s.burstDone || (preset.loop && local - s.lastBurst >= e.life[1])) {
          for (var i = 0; i < e.burst; i++) {
            _spawn(e, s);
          }
          s.burstDone = true;
          s.lastBurst = local;
        }
      } else if (e.rate > 0 && (preset.loop || local - dt < preset.duration)) {
        s.carry += e.rate * dt;
        while (s.carry >= 1) {
          s.carry -= 1;
          _spawn(e, s);
        }
      }
    }
    final keep = math.exp(-e.drag * dt);
    final list = s.particles;
    for (var i = list.length - 1; i >= 0; i--) {
      final p = list[i];
      p.age += dt;
      if (p.age >= p.life) {
        list[i] = list.last;
        list.removeLast();
        continue;
      }
      p.vy += e.gravity * unit * dt;
      p.vx *= keep;
      p.vy *= keep;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.rot += p.spin * dt;
    }
  }

  void _spawn(VfxEmitter e, _EmitterState s) {
    final tex = textures[e.texture];
    if (tex == null) return;
    final r = _random;
    var x = 0.0;
    var y = 0.0;
    final box = e.box;
    if (box != null) {
      x = box[0].toDouble() + (r.nextDouble() * 2 - 1) * box[2];
      y = box[1].toDouble() + (r.nextDouble() * 2 - 1) * box[3];
    }
    final radius = ((e.shape['radius'] as num?) ?? 0).toDouble() * unit;
    var angle = r.nextDouble() * 2 * math.pi;
    switch (e.shape['type']) {
      case 'sphere':
        final a = r.nextDouble() * 2 * math.pi;
        final d = radius * math.sqrt(r.nextDouble());
        x += math.cos(a) * d;
        y += math.sin(a) * d;
      case 'circle':
        final thick = ((e.shape['thickness'] as num?) ?? 1).toDouble();
        final inner = radius * (1 - thick);
        final a = r.nextDouble() * 2 * math.pi;
        final d = inner + (radius - inner) * math.sqrt(r.nextDouble());
        x += math.cos(a) * d;
        y += math.sin(a) * d;
      case 'cone':
        x += (r.nextDouble() * 2 - 1) * radius;
        final cone = ((e.shape['angle'] as num?) ?? 0).toDouble();
        angle = -math.pi / 2 + (r.nextDouble() * 2 - 1) * cone * _deg;
    }
    final dir = e.direction;
    if (dir != null) {
      angle =
          math.atan2(dir[1], dir[0]) +
          (r.nextDouble() * 2 - 1) * e.spread * _deg;
    }
    final speed = _between(e.speed) * unit;
    if (e.worldSpace) {
      x += absolutePosition.x;
      y += absolutePosition.y;
    }
    s.particles.add(
      _Particle(
        x,
        y,
        math.cos(angle) * speed,
        math.sin(angle) * speed,
        _between(e.life),
        _between(e.rotation),
        _between(e.spin),
        _between(e.size) * unit,
        r.nextInt(tex.cols * tex.rows),
      ),
    );
  }

  static double _curve(List<List<double>> pts, double t) {
    if (t <= pts.first[0]) return pts.first[1];
    for (var i = 1; i < pts.length; i++) {
      if (t <= pts[i][0]) {
        final a = pts[i - 1];
        final b = pts[i];
        final span = b[0] - a[0];
        return span <= 0 ? b[1] : a[1] + (b[1] - a[1]) * (t - a[0]) / span;
      }
    }
    return pts.last[1];
  }

  static ui.Color _color(List<List<double>> stops, double t) {
    List<double> at = stops.last;
    if (t <= stops.first[0]) {
      at = stops.first;
    } else {
      for (var i = 1; i < stops.length; i++) {
        if (t <= stops[i][0]) {
          final a = stops[i - 1];
          final b = stops[i];
          final k = (t - a[0]) / math.max(b[0] - a[0], 1e-6);
          at = [for (var c = 0; c < 5; c++) a[c] + (b[c] - a[c]) * k];
          break;
        }
      }
    }
    return ui.Color.fromARGB(
      _byte(at[4]),
      _byte(at[1]),
      _byte(at[2]),
      _byte(at[3]),
    );
  }

  @override
  void render(ui.Canvas canvas) {
    for (var i = 0; i < _states.length; i++) {
      final e = preset.emitters[i];
      final tex = textures[e.texture];
      final list = _states[i].particles;
      if (tex == null || list.isEmpty) continue;
      final cw = tex.cellWidth;
      final ch = tex.cellHeight;
      final transforms = <ui.RSTransform>[];
      final rects = <ui.Rect>[];
      final colors = <ui.Color>[];
      for (final p in list) {
        final t = p.age / p.life;
        final scale = p.size * _curve(e.sizeCurve, t) / cw;
        if (scale <= 0) continue;
        var x = p.x;
        var y = p.y;
        if (e.worldSpace) {
          final local = absoluteToLocal(Vector2(x, y));
          x = local.x;
          y = local.y;
        }
        final rot = e.velocityAligned ? math.atan2(p.vx, -p.vy) : p.rot * _deg;
        rects.add(
          ui.Rect.fromLTWH(
            (p.frame % tex.cols) * cw,
            (p.frame ~/ tex.cols) * ch,
            cw,
            ch,
          ),
        );
        transforms.add(
          ui.RSTransform.fromComponents(
            rotation: rot,
            scale: scale,
            anchorX: cw / 2,
            anchorY: ch / 2,
            translateX: x,
            translateY: y,
          ),
        );
        colors.add(_color(e.colors, t));
      }
      if (rects.isEmpty) continue;
      canvas.drawAtlas(
        tex.image,
        transforms,
        rects,
        colors,
        ui.BlendMode.modulate,
        null,
        e.additive ? _add : _alpha,
      );
    }
  }
}

/// A scrolling, tiled mist texture that fills [size]; [scroll] is in tiles per second.
class MistComponent extends PositionComponent {
  MistComponent(
    this.image, {
    required Vector2 size,
    required this.tiles,
    required this.scroll,
    this.tint = const ui.Color(0xFFDDE8F2),
    this.density = 1,
  }) : super(size: size);

  final ui.Image image;
  final Vector2 tiles;
  final Vector2 scroll;
  final ui.Color tint;
  final double density;
  double _t = 0;

  @override
  void update(double dt) => _t += dt;

  @override
  void render(ui.Canvas canvas) {
    final tw = size.x / tiles.x;
    final th = size.y / tiles.y;
    final ox = (_t * scroll.x % 1) * tw;
    final oy = (_t * scroll.y % 1) * th;
    final paint = ui.Paint()
      ..filterQuality = ui.FilterQuality.low
      ..colorFilter = ui.ColorFilter.mode(
        tint.withValues(alpha: density),
        ui.BlendMode.modulate,
      );
    final src = ui.Rect.fromLTWH(
      0,
      0,
      image.width.toDouble(),
      image.height.toDouble(),
    );
    canvas.save();
    canvas.clipRect(ui.Rect.fromLTWH(0, 0, size.x, size.y));
    for (var y = -1; y <= tiles.y.ceil(); y++) {
      for (var x = -1; x <= tiles.x.ceil(); x++) {
        canvas.drawImageRect(
          image,
          src,
          ui.Rect.fromLTWH(x * tw + ox, y * th + oy, tw, th),
          paint,
        );
      }
    }
    canvas.restore();
  }
}

/// `assets/vfx/<name>/<name>_vfx.json` (particle presets) or `assets/weather/<name>/<name>_weather.json`: textures
/// plus presets, ready to [spawn].
class VfxLibrary {
  VfxLibrary._(this.presets, this.textures, this.unit, this.mists);

  final Map<String, VfxPreset> presets;
  final Map<String, VfxTexture> textures;
  final double unit;
  final Map<String, Map<String, dynamic>> mists;

  static Future<VfxLibrary> load(String name) => _load('vfx', name, 'vfx');

  /// Weather overlay: presets are the particle layers (screen pixels), [mist] builds the fog layers.
  static Future<VfxLibrary> loadWeather(String name) =>
      _load('weather', name, 'weather');

  static Future<VfxLibrary> _load(String dir, String name, String stem) async {
    final doc = jsonDecode(
      await rootBundle.loadString('assets/$dir/$name/${name}_$stem.json'),
    ) as Map<String, dynamic>;
    final images = Images(prefix: 'assets/$dir/$name/');
    final textures = <String, VfxTexture>{};
    for (final entry in (doc['textures'] as Map<String, dynamic>).entries) {
      final t = entry.value as Map<String, dynamic>;
      textures[entry.key] = VfxTexture(
        await images.load(t['file'] as String),
        ((t['cols'] as num?) ?? 1).toInt(),
        ((t['rows'] as num?) ?? 1).toInt(),
      );
    }
    final presets = <String, VfxPreset>{};
    for (final raw in doc['presets'] as List<dynamic>) {
      final preset = VfxPreset.fromJson(raw as Map<String, dynamic>);
      presets[preset.name] = preset;
    }
    final mists = <String, Map<String, dynamic>>{
      for (final raw in (doc['mists'] as List<dynamic>?) ?? const [])
        (raw as Map<String, dynamic>)['name'] as String: raw,
    };
    return VfxLibrary._(presets, textures, _num(doc['ppu']), mists);
  }

  VfxComponent spawn(
    String preset, {
    Vector2? position,
    bool autoRemove = true,
    bool prewarm = false,
    int? seed,
  }) {
    final spec = presets[preset];
    if (spec == null) throw ArgumentError.value(preset, 'preset', 'unknown');
    final component = VfxComponent(
      spec,
      textures,
      unit,
      autoRemove: autoRemove,
      seed: seed,
      position: position,
    );
    if (prewarm) component.prewarm();
    return component;
  }

  MistComponent mist(String layer, Vector2 size) {
    final spec = mists[layer];
    final tex = spec == null ? null : textures[spec['texture'] as String];
    if (spec == null || tex == null) {
      throw ArgumentError.value(layer, 'layer', 'unknown');
    }
    final tint = (spec['tint'] as String).substring(1);
    return MistComponent(
      tex.image,
      size: size,
      tiles: Vector2(_num(spec['tiles'][0]), _num(spec['tiles'][1])),
      scroll: Vector2(_num(spec['scroll'][0]), _num(spec['scroll'][1])),
      tint: ui.Color(0xFF000000 | int.parse(tint.substring(0, 6), radix: 16)),
      density: math.min(1, _num(spec['density'])),
    );
  }
}
"""

WIDGETS_DART = GEN_HEAD + r"""import 'package:flutter/widgets.dart';

/// A point of interest on a minimap: [x] / [y] are 0..1 from the top-left corner of the map.
class MinimapPoi {
  const MinimapPoi(this.kind, this.x, this.y);

  final String kind;
  final double x;
  final double y;
}

/// Minimap image with its points of interest laid over it; [marker] draws one point.
class MinimapView extends StatelessWidget {
  const MinimapView({
    super.key,
    required this.asset,
    required this.pois,
    required this.marker,
    this.size = 160,
    this.markerSize = 12,
  });

  final String asset;
  final List<MinimapPoi> pois;
  final Widget Function(BuildContext context, MinimapPoi poi) marker;
  final double size;
  final double markerSize;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: size,
      height: size,
      child: Stack(
        children: [
          Positioned.fill(child: Image.asset(asset, fit: BoxFit.fill)),
          for (final poi in pois)
            Positioned(
              left: poi.x * size - markerSize / 2,
              top: poi.y * size - markerSize / 2,
              width: markerSize,
              height: markerSize,
              child: marker(context, poi),
            ),
        ],
      ),
    );
  }
}

/// One card of a card set: its baked front image and (when kept) the art alone.
class CardEntry {
  const CardEntry(this.id, this.name, this.front, this.art);

  final String id;
  final String name;
  final String front;
  final String? art;
}

/// Card front or back, [width] wide (cards keep their 5:7 look through the image's own aspect).
class CardFace extends StatelessWidget {
  const CardFace({super.key, required this.asset, this.width = 200});

  final String asset;
  final double width;

  @override
  Widget build(BuildContext context) =>
      Image.asset(asset, width: width, fit: BoxFit.contain);
}
"""
