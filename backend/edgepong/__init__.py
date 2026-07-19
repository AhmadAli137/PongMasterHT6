"""Edge Pong backend package.

Authoritative edge game service: camera/AprilTag pose, paddle IMU telemetry,
pose fusion, fixed-step physics + swept collision, four-quadrant haptics, and a
WebSocket state server for the Three.js renderer.

Every hardware-dependent subsystem ships with a simulation mode so the full
vertical slice runs with no physical hardware attached.
"""

__version__ = "0.1.0"
