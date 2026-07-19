// Shared message types matching the backend WebSocket schema (spec §19.4).

export type Vec3 = [number, number, number];
export type Quat = [number, number, number, number]; // w, x, y, z

export type TrackingState = "GOOD" | "DEGRADED" | "LOST";
export type BallType = "NORMAL" | "SMASH" | "BACKHAND" | "AVOID";
export type HitQuality = "LATE" | "GOOD" | "PERFECT";

export interface BallMsg {
  id: number;
  position: Vec3;
  velocity: Vec3;
  spin?: Vec3; // rad/s; drives the curved predicted-trajectory line
  radius: number;
  type: BallType;
  state: string;
}

export interface ServeMsg {
  position: Vec3; // where the next ball will launch from
  inMs: number;   // time until it launches
}

export interface StateMsg {
  type: "state";
  serverTimeUs: number;
  gameState: string;
  countdown: number;
  remainingMs: number;
  balanceMode?: boolean;
  calibrationProgress?: number;
  controlMode?: "MOUSE" | "AUTO" | "HARDWARE";
  serve?: ServeMsg | null;
  opponentSwing?: number; // increments on every opponent stroke (serve/return)
  stance?: "FOREHAND" | "BACKHAND";
  server?: "PLAYER" | "OPPONENT"; // whose serve this point is
  playerServe?: { phase: "AIM" | "COUNTDOWN"; dropInMs: number } | null;
  paddle: {
    position: Vec3;
    quaternion: Quat;
    confidence: number;
    trackingState: TrackingState;
  };
  balls: BallMsg[];
  score: number;
  combo: number;
  bestCombo: number;
  accuracy: number;
}

export interface ImpactMsg {
  type: "impact";
  id: number;
  ballId: number;
  timeUs: number;
  position: Vec3;
  localX: number;
  localY: number;
  strength: number;
  quality: HitQuality;
  ballType: BallType;
  scoreDelta: number;
}

export type RallyOutcome = "IN" | "NET" | "OUT" | "OWN_SIDE" | "WINNER" | "MISS";

export interface RallyMsg {
  type: "rally";
  outcome: RallyOutcome;
  scoreDelta: number;
}

export type ServerMsg = StateMsg | ImpactMsg | RallyMsg;
