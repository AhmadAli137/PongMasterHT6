# 🏓 Edge Pong

Swing a real paddle at virtual balls — and feel every hit.

Weekend hackathon build, in progress.

## The fun math: quaternions

The paddle can point any direction in 3D, so we track its orientation with
**quaternions** — four numbers that mean "spin by *this* angle, around *this*
axis."

Why not plain pitch / yaw / roll? Those jam up when two axes line up ("gimbal
lock") and blend badly mid-motion. Quaternions never jam and blend smoothly —
exactly what you want for a paddle whipping through a swing.
