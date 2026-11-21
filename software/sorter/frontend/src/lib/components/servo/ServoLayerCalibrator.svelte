<script lang="ts">
	import ChevronLeft from '@lucide/svelte/icons/chevron-left';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Lock from '@lucide/svelte/icons/lock';
	import LockOpen from '@lucide/svelte/icons/lock-open';
	import Layers from '@lucide/svelte/icons/layers';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash';
	import Eraser from '@lucide/svelte/icons/eraser';
	import { _ } from 'svelte-i18n';
	import ServoSpeedSettings from './ServoSpeedSettings.svelte';
	import ChuteFlapDiagram from './ChuteFlapDiagram.svelte';
	import FlapOpenIcon from './FlapOpenIcon.svelte';
	import FlapClosedIcon from './FlapClosedIcon.svelte';
	import { userConfig } from '$lib/stores/userConfig.svelte';
	import { onMount } from 'svelte';
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import { getMachinesContext } from '$lib/machines/context';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Select from '$lib/components/ui/Select.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Checkbox from '$lib/components/ui/Checkbox.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import Switch from '$lib/components/ui/Switch.svelte';
	import EmptyState from '$lib/components/ui/EmptyState.svelte';

	type ServoBackend = 'pca9685' | 'waveshare' | 'waveshare_ble';
	type BleGateState = 'open' | 'closed' | 'blocked' | 'unknown' | 'error' | undefined;

	type LayerDraft = {
		layerIndex: number; // 0-based, matches backend layer/servo index
		label: string;
		enabled: boolean;
		channel: string; // PCA channel id as string ('' = unassigned)
		// The channel as last loaded from the backend; null for a layer added here.
		savedChannel: string | null;
		invert: boolean;
		binCount: string;
		maxPiecesPerBin: string;
		openAngle: number | null;
		closedAngle: number | null;
		currentAngle: number | null;
		// Channel of the servo the running hardware drives for this layer; null
		// when it has none (a layer added since the last home).
		liveChannel: number | null;
		busy: boolean;
		lockStatus: 'idle' | 'saving' | 'saved' | 'error';
		lockError: string;
		// Chute-settle wait (how long Sending waits after a drop before the
		// chute may move again) - physics-computed per layer, plus an
		// operator fine-tune on top. settleAdjustMs is editable here; the
		// other two are read-only, shown for context.
		settleBaseMs: number | null;
		settleAdjustMs: string; // '' = 0, kept as a string for the number input
		settleTotalMs: number | null;
	};

	const manager = getMachinesContext();

	let loadedMachineKey = $state('');
	let loading = $state(false);
	let saving = $state(false);
	let errorMsg = $state<string | null>(null);
	let statusMsg = $state('');
	let backend = $state<ServoBackend>('pca9685');
	// Only meaningful for waveshare_ble (the bridge board's BLE address) -
	// save_servo_hardware_config requires it on every save for that backend,
	// so it has to round-trip even though this page never edits it itself.
	let device = $state('');
	let layers = $state<LayerDraft[]>([]);
	let channelChoices = $state<number[]>([]);
	let allowedCounts = $state<number[]>([6, 12, 18, 30]);
	let selectedIndex = $state<number | null>(null);

	// Waveshare BLE: each layer's "channel" field (reused as-is) holds a gate
	// number instead of a PCA9685 channel - invert and the angle-degree nudge/
	// lock controls below don't apply (the bridge firmware finds and keeps its
	// own open/closed limits; see esp32_ble_servo_bridge). This block is the
	// live bridge/gate state for the BLE-specific controls further down.
	let bleConnected = $state(false);
	let bleStatusLoading = $state(false);
	let bleLastError = $state<string | null>(null);
	let gateStateByGate = $state<Record<number, BleGateState>>({});
	let gateBusyByGate = $state<Record<number, string>>({});
	// Per-gate calibrated flag, derived from the firmware's own "ERR
	// UNCALIBRATED" response text (cmdOpenClose/cmdStatus in
	// esp32_ble_servo_bridge) rather than the PCA9685-only open/closed-angle
	// fields - those stay null for BLE gates, which otherwise left the top
	// badge stuck on "Needs calibrating" forever even after a real calibrate.
	let gateCalibratedByGate = $state<Record<number, boolean>>({});

	function noteGateRaw(gate: number, raw: string) {
		if (raw.includes('UNCALIBRATED')) {
			gateCalibratedByGate = { ...gateCalibratedByGate, [gate]: false };
		} else if (!raw.includes('OFFLINE')) {
			// OPEN/CLOSED/BLOCKED/UNKNOWN all require calibrated limits to be
			// reported at all - "OFFLINE" alone says nothing either way.
			gateCalibratedByGate = { ...gateCalibratedByGate, [gate]: true };
		}
	}
	// Channel, invert and the speeds: set once when the machine is built, so they
	// stay out of the way of calibrating.
	let advanced = $derived(userConfig.servoAdvanced);

	// Signature of everything the "Save servo layers" button persists (layer
	// add/remove, channel, invert, bin count, max/bin, active). Captured at load
	// and after each save; `dirty` flags the form whenever the two diverge. Open/
	// closed angles and speeds persist through their own endpoints, so they're
	// deliberately left out — they don't dirty this form.
	let savedSignature = $state('');
	function layerSignature(list: LayerDraft[]): string {
		return JSON.stringify(
			list.map((l) => [
				l.enabled,
				l.channel,
				l.invert,
				l.binCount,
				l.maxPiecesPerBin.trim(),
				l.settleAdjustMs.trim()
			])
		);
	}
	let dirty = $derived(layerSignature(layers) !== savedSignature);

	// Global servo speeds (°/s). null = not overridden (firmware default applies).
	let openSpeed = $state<number | null>(null);
	let closeSpeed = $state<number | null>(null);
	let homingSpeed = $state<number | null>(null);

	// Global angles are no longer used to drive PCA servos, but the /servo
	// endpoint still round-trips them, so preserve whatever is stored.
	let globalOpenAngle: number | null = null;
	let globalClosedAngle: number | null = null;
	let port: string | null = null;

	function currentBackendBaseUrl(): string {
		return (
			machineHttpBaseUrlFromWsUrl(
				manager.selectedMachine?.status === 'connected' ? manager.selectedMachine.url : null
			) ?? getBackendHttpBase()
		);
	}

	function layerLabel(index: number): string {
		return $_('components.servo.servoLayerCalibrator.layerLabel', { values: { n: index + 1 } });
	}

	function layerIsCalibrated(layer: LayerDraft): boolean {
		return layer.openAngle !== null && layer.closedAngle !== null;
	}

	function layerHasChannel(layer: LayerDraft): boolean {
		return layer.channel.trim().length > 0;
	}

	// The live servos are built when the machine homes, so a layer added or
	// rewired since then can't move until it is saved and the machine homed.
	function layerCanMove(layer: LayerDraft): boolean {
		return layer.liveChannel !== null && layer.channel === String(layer.liveChannel);
	}

	function servoHint(layer: LayerDraft): string | null {
		if (layerCanMove(layer)) return null;
		if (!layerHasChannel(layer)) {
			return $_('components.servo.servoLayerCalibrator.hint.noChannel');
		}
		if (layer.savedChannel !== layer.channel) {
			return $_('components.servo.servoLayerCalibrator.hint.unsaved');
		}
		return $_('components.servo.servoLayerCalibrator.hint.needsHome');
	}

	// What to do next on a layer that is not calibrated yet: open first, then closed.
	function nextStep(layer: LayerDraft): 'open' | 'closed' | null {
		if (layer.openAngle === null) return 'open';
		if (layer.closedAngle === null) return 'closed';
		return null;
	}

	async function loadSettings() {
		loading = true;
		errorMsg = null;
		try {
			const res = await fetch(`${currentBackendBaseUrl()}/api/hardware-config`);
			if (!res.ok) throw new Error(await res.text());
			const payload = await res.json();
			const servo = payload?.servo ?? {};
			backend =
				servo.backend === 'waveshare' || servo.backend === 'waveshare_ble'
					? servo.backend
					: 'pca9685';
			globalOpenAngle = typeof servo.open_angle === 'number' ? servo.open_angle : null;
			globalClosedAngle = typeof servo.closed_angle === 'number' ? servo.closed_angle : null;
			openSpeed = typeof servo.open_speed === 'number' ? servo.open_speed : null;
			closeSpeed = typeof servo.close_speed === 'number' ? servo.close_speed : null;
			homingSpeed = typeof servo.homing_speed === 'number' ? servo.homing_speed : null;
			port = typeof servo.port === 'string' ? servo.port : null;
			device = typeof servo.device === 'string' ? servo.device : '';
			channelChoices = Array.isArray(servo.available_channel_ids)
				? servo.available_channel_ids
						.map((value: any) => Number(value))
						.filter((value: number) => Number.isInteger(value))
				: [];

			const storage = payload?.storage_layers ?? {};
			allowedCounts = Array.isArray(storage.allowed_bin_counts)
				? storage.allowed_bin_counts.filter((v: unknown): v is number => typeof v === 'number')
				: [6, 12, 18, 30];

			const servoChannels: Array<{ id: number | null; invert: boolean }> = Array.isArray(
				servo.channels
			)
				? servo.channels
				: [];
			const storageLayers: any[] = Array.isArray(storage.layers) ? storage.layers : [];

			const count = Math.max(
				servoChannels.length,
				storageLayers.length,
				Number(servo.layer_count ?? 0)
			);
			const next: LayerDraft[] = [];
			for (let i = 0; i < count; i++) {
				const channel = servoChannels[i];
				const sl = storageLayers[i] ?? {};
				next.push({
					layerIndex: i,
					label: layerLabel(i),
					enabled: Boolean(sl.enabled ?? true),
					channel: channel && typeof channel.id === 'number' ? String(channel.id) : '',
					savedChannel: channel && typeof channel.id === 'number' ? String(channel.id) : '',
					invert: Boolean(channel?.invert),
					binCount: String(Number(sl.bin_count ?? 12)),
					maxPiecesPerBin:
						typeof sl.max_pieces_per_bin === 'number' && sl.max_pieces_per_bin > 0
							? String(sl.max_pieces_per_bin)
							: '',
					openAngle: typeof sl.servo_open_angle === 'number' ? sl.servo_open_angle : null,
					closedAngle: typeof sl.servo_closed_angle === 'number' ? sl.servo_closed_angle : null,
					currentAngle: typeof sl.servo_current_angle === 'number' ? sl.servo_current_angle : null,
					liveChannel: typeof sl.servo_live_channel === 'number' ? sl.servo_live_channel : null,
					busy: false,
					lockStatus: 'idle',
					lockError: '',
					settleBaseMs: typeof sl.settle_base_ms === 'number' ? sl.settle_base_ms : null,
					settleAdjustMs:
						typeof sl.settle_adjust_ms === 'number' && sl.settle_adjust_ms !== 0
							? String(sl.settle_adjust_ms)
							: '',
					settleTotalMs: typeof sl.settle_total_ms === 'number' ? sl.settle_total_ms : null
				});
			}
			layers = next;
			savedSignature = layerSignature(next);
			if (selectedIndex !== null && selectedIndex >= layers.length) selectedIndex = null;
			if (backend === 'waveshare_ble') await loadBleStatus();
		} catch (e: any) {
			errorMsg = e.message ?? $_('components.servo.servoLayerCalibrator.loadFailed');
		} finally {
			loading = false;
		}
	}

	async function loadBleStatus() {
		bleStatusLoading = true;
		try {
			const res = await fetch(`${currentBackendBaseUrl()}/api/hardware-config/waveshare-ble/status`);
			if (!res.ok) throw new Error(await res.text());
			const payload = await res.json();
			bleConnected = Boolean(payload?.connected);
			bleLastError = typeof payload?.last_error === 'string' ? payload.last_error : null;
		} catch (e: any) {
			bleLastError = e?.message ?? $_('components.servo.servoLayerCalibrator.ble.errors.statusFailed');
			bleConnected = false;
		} finally {
			bleStatusLoading = false;
		}
		// Known-open/closed state per assigned gate, so the page shows what the
		// bridge currently reports rather than "untested" until the operator
		// presses a button.
		await Promise.all(
			layers
				.filter((l) => layerHasChannel(l))
				.map(async (l) => {
					const gate = Number(l.channel);
					try {
						const res = await fetch(
							`${currentBackendBaseUrl()}/api/hardware-config/waveshare-ble/gates/${gate}/status`
						);
						if (!res.ok) return;
						const payload = await res.json();
						const state = String(payload?.state ?? '').toLowerCase();
						gateStateByGate = {
							...gateStateByGate,
							[gate]: (['open', 'closed', 'blocked'].includes(state) ? state : 'unknown') as BleGateState
						};
						if (typeof payload?.raw === 'string') noteGateRaw(gate, payload.raw);
					} catch {
						// Leave this gate's state as whatever it was - a single failed
						// poll shouldn't blank out an otherwise-working page.
					}
				})
		);
	}

	function setGateBusy(gate: number, kind: string | null) {
		if (kind === null) {
			const next = { ...gateBusyByGate };
			delete next[gate];
			gateBusyByGate = next;
		} else {
			gateBusyByGate = { ...gateBusyByGate, [gate]: kind };
		}
	}

	async function moveBleGate(gate: number, position: 'open' | 'close') {
		setGateBusy(gate, position === 'open' ? 'opening' : 'closing');
		bleLastError = null;
		try {
			const res = await fetch(
				`${currentBackendBaseUrl()}/api/hardware-config/waveshare-ble/gates/${gate}/move`,
				{
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({ position })
				}
			);
			if (!res.ok) throw new Error(await res.text());
			const payload = await res.json();
			const state = String(payload?.state ?? '').toLowerCase();
			gateStateByGate = {
				...gateStateByGate,
				[gate]: (['open', 'closed', 'blocked'].includes(state) ? state : 'unknown') as BleGateState
			};
			if (typeof payload?.raw === 'string') noteGateRaw(gate, payload.raw);
		} catch (e: any) {
			bleLastError =
				e?.message ??
				$_('components.servo.servoLayerCalibrator.ble.errors.moveGateFailed', { values: { gate } });
			gateStateByGate = { ...gateStateByGate, [gate]: 'error' };
		} finally {
			setGateBusy(gate, null);
		}
	}

	async function calibrateBleGate(gate: number) {
		setGateBusy(gate, 'calibrating');
		bleLastError = null;
		statusMsg = '';
		try {
			const res = await fetch(
				`${currentBackendBaseUrl()}/api/hardware-config/waveshare-ble/gates/${gate}/calibrate`,
				{ method: 'POST' }
			);
			if (!res.ok) throw new Error(await res.text());
			const payload = await res.json();
			if (typeof payload?.raw === 'string') noteGateRaw(gate, payload.raw);
			statusMsg =
				payload?.message ??
				$_('components.servo.servoLayerCalibrator.ble.status.gateCalibrated', { values: { gate } });
		} catch (e: any) {
			bleLastError =
				e?.message ??
				$_('components.servo.servoLayerCalibrator.ble.errors.calibrateGateFailed', {
					values: { gate }
				});
		} finally {
			setGateBusy(gate, null);
		}
	}

	function bleBadgeTone(state: BleGateState): 'neutral' | 'success' | 'info' | 'danger' {
		if (state === 'open') return 'success';
		if (state === 'closed') return 'info';
		if (state === 'blocked' || state === 'error') return 'danger';
		return 'neutral';
	}

	function bleBadgeLabel(state: BleGateState): string {
		if (state === 'open') return $_('components.servo.servoLayerCalibrator.ble.gateState.open');
		if (state === 'closed') return $_('components.servo.servoLayerCalibrator.ble.gateState.closed');
		if (state === 'blocked') return $_('components.servo.servoLayerCalibrator.ble.gateState.blocked');
		if (state === 'error') return $_('components.servo.servoLayerCalibrator.ble.gateState.error');
		if (state === 'unknown') return $_('components.servo.servoLayerCalibrator.ble.gateState.unknown');
		return $_('components.servo.servoLayerCalibrator.ble.gateState.untested');
	}

	function setLayer(layerIndex: number, patch: Partial<LayerDraft>) {
		layers = layers.map((layer) =>
			layer.layerIndex === layerIndex ? { ...layer, ...patch } : layer
		);
	}

	async function postLayerAction(
		layerIndex: number,
		path: string,
		body: unknown
	): Promise<any | null> {
		errorMsg = null;
		statusMsg = '';
		setLayer(layerIndex, { busy: true });
		try {
			const res = await fetch(
				`${currentBackendBaseUrl()}/api/hardware-config/servo/layers/${layerIndex}/${path}`,
				{
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify(body)
				}
			);
			if (!res.ok) throw new Error(await res.text());
			return await res.json();
		} catch (e: any) {
			errorMsg =
				e.message ??
				$_('components.servo.servoLayerCalibrator.actionFailed', {
					values: { action: path, layer: layerIndex + 1 }
				});
			return null;
		} finally {
			setLayer(layerIndex, { busy: false });
		}
	}

	// A press of an arrow (button, or up/down key) moves a flap 1°; left/right
	// keys move it 5°. Holding a button repeats, and the steps grow the longer
	// it is held.
	const FINE_STEP = 1;
	const COARSE_STEP = 5;
	const HOLD_DELAY_MS = 400;
	const HOLD_REPEAT_MS = 120;
	let holdTimer: ReturnType<typeof setTimeout> | null = null;
	let holding = false;

	async function jog(layerIndex: number, degrees: number) {
		selectedIndex = layerIndex;
		const result = await postLayerAction(layerIndex, 'nudge', { degrees });
		if (result && typeof result.new_angle === 'number') {
			setLayer(layerIndex, { currentAngle: result.new_angle });
		}
	}

	function holdStep(heldMs: number): number {
		if (heldMs > 2500) return 5;
		if (heldMs > 1200) return 2;
		return FINE_STEP;
	}

	function startHold(event: PointerEvent, layerIndex: number, direction: 1 | -1) {
		if (event.button !== 0) return;
		stopHold();
		holding = true;
		const startedAt = performance.now();
		const repeat = async () => {
			if (!holding) return;
			await jog(layerIndex, direction * holdStep(performance.now() - startedAt));
			if (holding) holdTimer = setTimeout(repeat, HOLD_REPEAT_MS);
		};
		void jog(layerIndex, direction * FINE_STEP);
		holdTimer = setTimeout(repeat, HOLD_DELAY_MS);
	}

	function stopHold() {
		holding = false;
		if (holdTimer !== null) clearTimeout(holdTimer);
		holdTimer = null;
	}

	// A pointer press is handled by startHold; this is the keyboard's Enter/Space.
	function keyboardClick(event: MouseEvent, layerIndex: number, direction: 1 | -1) {
		if (event.detail === 0) void jog(layerIndex, direction * FINE_STEP);
	}

	async function lockAngle(layerIndex: number, which: 'open' | 'closed') {
		// The /lock endpoint persists the angle to the backend immediately, so
		// locking is the save — the per-layer indicator reflects that write.
		setLayer(layerIndex, { lockStatus: 'saving', lockError: '' });
		const result = await postLayerAction(layerIndex, 'lock', { which });
		if (result) {
			setLayer(layerIndex, {
				openAngle: typeof result.open_angle === 'number' ? result.open_angle : null,
				closedAngle: typeof result.closed_angle === 'number' ? result.closed_angle : null,
				lockStatus: 'saved',
				lockError: ''
			});
			statusMsg =
				result.message ??
				$_(
					which === 'open'
						? 'components.servo.servoLayerCalibrator.status.lockedOpen'
						: 'components.servo.servoLayerCalibrator.status.lockedClosed',
					{ values: { layer: layerIndex + 1 } }
				);
		} else {
			setLayer(layerIndex, {
				lockStatus: 'error',
				lockError: errorMsg ?? $_('components.servo.servoLayerCalibrator.status.saveFailed')
			});
		}
	}

	async function clearAngles(layerIndex: number) {
		// Reset this layer's calibration back to unknown — both open and closed
		// angles are persisted as null on the backend.
		setLayer(layerIndex, { lockStatus: 'saving', lockError: '' });
		const result = await postLayerAction(layerIndex, 'clear', { which: 'both' });
		if (result) {
			setLayer(layerIndex, {
				openAngle: typeof result.open_angle === 'number' ? result.open_angle : null,
				closedAngle: typeof result.closed_angle === 'number' ? result.closed_angle : null,
				lockStatus: 'idle',
				lockError: ''
			});
			statusMsg =
				result.message ??
				$_('components.servo.servoLayerCalibrator.status.cleared', {
					values: { layer: layerIndex + 1 }
				});
		} else {
			setLayer(layerIndex, {
				lockStatus: 'error',
				lockError: errorMsg ?? $_('components.servo.servoLayerCalibrator.status.clearFailed')
			});
		}
	}

	async function moveTo(layerIndex: number, angle: number | null) {
		if (angle === null) return;
		const result = await postLayerAction(layerIndex, 'move-to', { angle });
		if (result && typeof result.new_angle === 'number') {
			setLayer(layerIndex, { currentAngle: result.new_angle });
		}
	}

	async function saveSettings() {
		saving = true;
		errorMsg = null;
		statusMsg = '';
		try {
			const channels = layers.map((layer, index) => {
				const id = layer.channel.trim().length > 0 ? Number(layer.channel) : null;
				if (id !== null && (!Number.isInteger(id) || id < 0)) {
					throw new Error(
						$_('components.servo.servoLayerCalibrator.validation.invalidChannel', {
							values: { layer: index + 1 }
						})
					);
				}
				if (id === null && layer.enabled) {
					throw new Error(
						$_('components.servo.servoLayerCalibrator.validation.needsChannel', {
							values: { layer: index + 1 }
						})
					);
				}
				return { id, invert: layer.invert };
			});

			const storageLayers = layers.map((layer) => {
				const trimmed = layer.maxPiecesPerBin.trim();
				let maxPieces: number | null = null;
				if (trimmed.length > 0) {
					const parsed = Number(trimmed);
					if (!Number.isInteger(parsed) || parsed <= 0) {
						throw new Error(
							$_('components.servo.servoLayerCalibrator.validation.binLimit', {
								values: { label: layer.label }
							})
						);
					}
					maxPieces = parsed;
				}
				const settleTrimmed = layer.settleAdjustMs.trim();
				let settleAdjust = 0;
				if (settleTrimmed.length > 0) {
					const parsedSettle = Number(settleTrimmed);
					if (!Number.isInteger(parsedSettle)) {
						throw new Error(
							$_('components.servo.servoLayerCalibrator.validation.settleAdjust', {
								values: { label: layer.label }
							})
						);
					}
					settleAdjust = parsedSettle;
				}
				return {
					bin_count: Number(layer.binCount),
					enabled: layer.enabled,
					servo_open_angle: layer.openAngle,
					servo_closed_angle: layer.closedAngle,
					max_pieces_per_bin: maxPieces,
					settle_adjust_ms: settleAdjust
				};
			});

			const storageRes = await fetch(
				`${currentBackendBaseUrl()}/api/hardware-config/storage-layers`,
				{
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({ layers: storageLayers })
				}
			);
			if (!storageRes.ok) throw new Error(await storageRes.text());

			const servoRes = await fetch(`${currentBackendBaseUrl()}/api/hardware-config/servo`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					backend,
					device: backend === 'waveshare_ble' ? device.trim() : null,
					open_angle: globalOpenAngle,
					closed_angle: globalClosedAngle,
					open_speed: openSpeed,
					close_speed: closeSpeed,
					homing_speed: homingSpeed,
					port,
					channels
				})
			});
			if (!servoRes.ok) throw new Error(await servoRes.text());

			statusMsg = $_('components.servo.servoLayerCalibrator.status.saved');
			await loadSettings();
		} catch (e: any) {
			errorMsg =
				e.message ?? $_('components.servo.servoLayerCalibrator.status.saveSettingsFailed');
		} finally {
			saving = false;
		}
	}

	function nextChannel(): string {
		// Auto-assign the next channel after the highest one already used, so a
		// fresh layer lands on an unused channel without manual picking. Falls back
		// to the first available choice (or blank) when nothing is assigned yet.
		const used = layers
			.map((l) => (l.channel.trim().length > 0 ? Number(l.channel) : null))
			.filter((n): n is number => n !== null && Number.isInteger(n));
		if (used.length > 0) {
			const candidate = Math.max(...used) + 1;
			if (channelChoices.length === 0 || channelChoices.includes(candidate)) {
				return String(candidate);
			}
		}
		const firstFree = channelChoices.find((c) => !used.includes(c));
		return firstFree !== undefined ? String(firstFree) : '';
	}

	function addLayer() {
		const nextIndex = layers.length;
		layers = [
			...layers,
			{
				layerIndex: nextIndex,
				label: layerLabel(nextIndex),
				enabled: true,
				channel: nextChannel(),
				savedChannel: null,
				invert: false,
				binCount: String(allowedCounts[0] ?? 12),
				maxPiecesPerBin: '',
				openAngle: null,
				closedAngle: null,
				currentAngle: null,
				liveChannel: null,
				busy: false,
				lockStatus: 'idle',
				lockError: '',
				settleBaseMs: null,
				settleAdjustMs: '',
				settleTotalMs: null
			}
		];
	}

	function removeLayer(layerIndex: number) {
		layers = layers
			.filter((l) => l.layerIndex !== layerIndex)
			// A layer that shifts down no longer lines up with the live servo at its
			// new index.
			.map((l, i) => ({
				...l,
				layerIndex: i,
				label: layerLabel(i),
				liveChannel: i === l.layerIndex ? l.liveChannel : null
			}));
		if (selectedIndex === layerIndex) selectedIndex = null;
	}

	$effect(() => {
		const machineKey =
			(manager.selectedMachine?.status === 'connected' ? manager.selectedMachine.url : null) ??
			'__local__';
		if (machineKey !== loadedMachineKey) {
			loadedMachineKey = machineKey;
			void loadSettings();
		}
	});

	onMount(() => {
		function handleKeydown(event: KeyboardEvent) {
			if (selectedIndex === null) return;
			const layer = layers.find((l) => l.layerIndex === selectedIndex);
			if (!layer || !layerCanMove(layer)) return;
			if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement)
				return;
			const step = {
				ArrowDown: -FINE_STEP,
				ArrowUp: FINE_STEP,
				ArrowLeft: -COARSE_STEP,
				ArrowRight: COARSE_STEP
			}[event.key];
			if (step !== undefined) {
				event.preventDefault();
				void jog(selectedIndex, step);
			} else if (event.key === 'Escape') {
				selectedIndex = null;
			}
		}
		window.addEventListener('keydown', handleKeydown);
		return () => {
			window.removeEventListener('keydown', handleKeydown);
			stopHold();
		};
	});
</script>

{#snippet flapStep(layer: LayerDraft, which: 'open' | 'closed', canMove: boolean, idle: boolean)}
	{@const angle = which === 'open' ? layer.openAngle : layer.closedAngle}
	{@const next = nextStep(layer) === which}
	<div class="flex min-w-0 flex-col gap-1.5">
		<span class="text-sm text-ink">
			<span class="font-medium"
				>{which === 'open'
					? $_('components.servo.servoLayerCalibrator.openWord')
					: $_('components.servo.servoLayerCalibrator.closedWord')}</span
			>
			{#if angle === null}
				<span class="text-ink-muted"
					>· {$_('components.servo.servoLayerCalibrator.notSet')}</span
				>
			{:else}
				<span class="num text-ink-muted">· {angle}°</span>
			{/if}
		</span>
		<div class="flex flex-wrap items-center gap-1.5">
			<Button
				size="sm"
				variant={next && canMove ? 'primary' : 'secondary'}
				icon={which === 'open' ? FlapOpenIcon : FlapClosedIcon}
				disabled={idle || !canMove}
				onclick={() => lockAngle(layer.layerIndex, which)}
			>
				{which === 'open'
					? $_('components.servo.servoLayerCalibrator.lockOpenButton')
					: $_('components.servo.servoLayerCalibrator.lockClosedButton')}
			</Button>
			<Button
				size="sm"
				disabled={idle || !canMove || angle === null}
				onclick={() => moveTo(layer.layerIndex, angle)}
			>
				{which === 'open'
					? $_('components.servo.servoLayerCalibrator.goToOpenButton')
					: $_('components.servo.servoLayerCalibrator.goToClosedButton')}
			</Button>
		</div>
	</div>
{/snippet}

{#if backend === 'waveshare'}
	<Alert tone="warning">
		{$_('components.servo.servoLayerCalibrator.alert.wrongBackend')}
	</Alert>
{:else}
	<Panel title={$_('components.servo.servoLayerCalibrator.panelTitle.flap')}>
		<div class="flex flex-col gap-4 md:flex-row md:items-start md:gap-8">
			<div class="flex items-center gap-4">
				<ChuteFlapDiagram state="open" size={128} />
				<p class="max-w-56 text-sm text-ink-muted">
					<span class="font-medium text-ink"
						>{$_('components.servo.servoLayerCalibrator.flapDiagram.openLabel')}</span
					>
					{$_('components.servo.servoLayerCalibrator.flapDiagram.openDescription')}
				</p>
			</div>
			<div class="flex items-center gap-4">
				<ChuteFlapDiagram state="closed" size={128} />
				<p class="max-w-56 text-sm text-ink-muted">
					<span class="font-medium text-ink"
						>{$_('components.servo.servoLayerCalibrator.flapDiagram.closedLabel')}</span
					>
					{$_('components.servo.servoLayerCalibrator.flapDiagram.closedDescription')}
				</p>
			</div>
		</div>
		<p class="mt-4 text-sm text-ink-muted">
			{$_('components.servo.servoLayerCalibrator.instructions.moveFlap')}
		</p>
	</Panel>

	<Panel title={$_('components.servo.servoLayerCalibrator.panelTitle.layers')} flush>
		{#snippet actions()}
			{#if backend === 'waveshare_ble'}
				<Badge tone={bleConnected ? 'success' : 'neutral'} dot>
					{bleConnected
						? $_('components.servo.servoLayerCalibrator.ble.connected')
						: $_('components.servo.servoLayerCalibrator.ble.notConnected')}
				</Badge>
				<Button size="sm" loading={bleStatusLoading} onclick={() => void loadBleStatus()}>
					{$_('components.servo.servoLayerCalibrator.ble.rescan')}
				</Button>
			{:else}
				<Switch
					checked={advanced}
					labelledby="storage-layers-advanced"
					onchange={(on) => userConfig.setServoAdvanced(on)}
				/>
				<span id="storage-layers-advanced" class="text-sm text-ink"
					>{$_('components.servo.servoLayerCalibrator.advancedLabel')}</span
				>
			{/if}
			<Button size="sm" icon={Plus} disabled={loading || saving} onclick={addLayer}
				>{$_('components.servo.servoLayerCalibrator.addLayerButton')}</Button
			>
		{/snippet}

		{#if layers.length === 0 && !loading}
			<div class="px-(--pad-panel) pb-(--pad-panel)">
				<EmptyState
					icon={Layers}
					title={$_('components.servo.servoLayerCalibrator.emptyState.title')}
					>{$_('components.servo.servoLayerCalibrator.emptyState.body')}</EmptyState
				>
			</div>
		{:else if loading && layers.length === 0}
			<div class="flex items-center gap-2 px-(--pad-panel) pb-(--pad-panel) text-sm text-ink-muted">
				<Spinner size={14} /> {$_('components.servo.servoLayerCalibrator.loadingLayers')}
			</div>
		{/if}

		<div class="divide-y divide-line">
			{#each layers as layer (layer.layerIndex)}
				{@const calibrated =
					backend === 'waveshare_ble'
						? layerHasChannel(layer) && gateCalibratedByGate[Number(layer.channel)] === true
						: layerIsCalibrated(layer)}
				{@const selected = selectedIndex === layer.layerIndex}
				{@const idle = loading || saving || layer.busy}
				{@const canMove = layerCanMove(layer)}
				{@const hint = servoHint(layer)}
				{@const step = nextStep(layer)}
				<!-- Using a layer's arrows selects it; then the arrow keys move its flap. -->
				<div
					class="flex flex-col gap-3 px-(--pad-panel) py-(--pad-row)
						{selected ? 'bg-primary-soft' : ''} {layer.enabled ? '' : 'opacity-60'}"
				>
					<div class="flex flex-wrap items-center gap-2">
						<span class="text-sm font-semibold text-ink">{layer.label}</span>
						{#if !layer.enabled}
							<Badge>{$_('components.servo.servoLayerCalibrator.badge.off')}</Badge>
						{:else if calibrated}
							<Badge tone="success"
								><Lock size={12} />
								{$_('components.servo.servoLayerCalibrator.badge.calibrated')}</Badge
							>
						{:else}
							<Badge tone="warning"
								><LockOpen size={12} />
								{$_('components.servo.servoLayerCalibrator.badge.needsCalibrating')}</Badge
							>
						{/if}
						{#if selected && canMove}
							<Badge tone="primary"
								>{$_('components.servo.servoLayerCalibrator.badge.moveHint')}</Badge
							>
						{/if}
						<span class="ml-auto flex items-center gap-3">
							<Checkbox
								checked={layer.enabled}
								disabled={loading || saving}
								onchange={(e) =>
									setLayer(layer.layerIndex, {
										enabled: (e.currentTarget as HTMLInputElement).checked
									})}
							>
								{$_('components.servo.servoLayerCalibrator.activeLabel')}
							</Checkbox>
							<Button
								variant="ghost"
								size="sm"
								icon={Trash2}
								label={$_('components.servo.servoLayerCalibrator.removeLabel', {
									values: { label: layer.label }
								})}
								disabled={loading || saving}
								onclick={() => removeLayer(layer.layerIndex)}
							/>
						</span>
					</div>

					<div class="flex flex-wrap items-end gap-x-5 gap-y-3">
						<Field
							label={$_('components.servo.servoLayerCalibrator.bins.label')}
							for="layer-{layer.layerIndex}-bins"
							info={$_('components.servo.servoLayerCalibrator.bins.info', {
								values: { counts: allowedCounts.join(', ') }
							})}
						>
							<div class="w-20">
								<Select
									id="layer-{layer.layerIndex}-bins"
									size="sm"
									value={layer.binCount}
									disabled={loading || saving}
									onchange={(binCount) => setLayer(layer.layerIndex, { binCount })}
									options={allowedCounts.map((c) => ({ value: String(c), label: String(c) }))}
								/>
							</div>
						</Field>
						<Field
							label={$_('components.servo.servoLayerCalibrator.binLimit.label')}
							for="layer-{layer.layerIndex}-max"
							info={$_('components.servo.servoLayerCalibrator.binLimit.info')}
						>
							<Input
								id="layer-{layer.layerIndex}-max"
								type="number"
								size="sm"
								min={1}
								step={1}
								placeholder="∞"
								value={layer.maxPiecesPerBin}
								disabled={loading || saving}
								oninput={(e) =>
									setLayer(layer.layerIndex, {
										maxPiecesPerBin: (e.currentTarget as HTMLInputElement).value
									})}
								class="w-24"
							/>
						</Field>
						<Field
							label={$_('components.servo.servoLayerCalibrator.settle.label')}
							for="layer-{layer.layerIndex}-settle-adjust"
							info={$_('components.servo.servoLayerCalibrator.settle.info', {
								values: { base: layer.settleBaseMs ?? '--', total: layer.settleTotalMs ?? '--' }
							})}
						>
							<div class="flex items-center gap-1">
								<Input
									id="layer-{layer.layerIndex}-settle-adjust"
									type="number"
									size="sm"
									step={10}
									placeholder="0"
									value={layer.settleAdjustMs}
									disabled={loading || saving}
									oninput={(e) =>
										setLayer(layer.layerIndex, {
											settleAdjustMs: (e.currentTarget as HTMLInputElement).value
										})}
									class="w-20"
								/>
								<span class="text-sm text-ink-muted">ms</span>
							</div>
						</Field>
						{#if backend === 'waveshare_ble'}
							<Field
								label={$_('components.servo.servoLayerCalibrator.ble.gateLabel')}
								for="layer-{layer.layerIndex}-gate"
							>
								<div class="w-28">
									<Select
										id="layer-{layer.layerIndex}-gate"
										size="sm"
										value={layer.channel}
										disabled={loading || saving}
										onchange={(channel) => setLayer(layer.layerIndex, { channel })}
										options={[
											{ value: '', label: $_('components.servo.servoLayerCalibrator.ble.gateNone') },
											...channelChoices.map((c) => ({
												value: String(c),
												label: $_('components.servo.servoLayerCalibrator.ble.gateOption', {
													values: { id: c }
												})
											}))
										]}
									/>
								</div>
							</Field>
						{:else if advanced}
							<Field
								label={$_('components.servo.servoLayerCalibrator.channel.label')}
								for="layer-{layer.layerIndex}-channel"
								info={$_('components.servo.servoLayerCalibrator.channel.info')}
							>
								<div class="w-24">
									<Select
										id="layer-{layer.layerIndex}-channel"
										size="sm"
										value={layer.channel}
										disabled={loading || saving}
										onchange={(channel) => setLayer(layer.layerIndex, { channel })}
										options={[
											{
												value: '',
												label: $_('components.servo.servoLayerCalibrator.channel.none')
											},
											...channelChoices.map((c) => ({ value: String(c), label: String(c) }))
										]}
									/>
								</div>
							</Field>
							<div class="pb-1.5">
								<Checkbox
									checked={layer.invert}
									disabled={loading || saving}
									onchange={(e) =>
										setLayer(layer.layerIndex, {
											invert: (e.currentTarget as HTMLInputElement).checked
										})}
								>
									{$_('components.servo.servoLayerCalibrator.invertLabel')}
								</Checkbox>
							</div>
						{/if}
					</div>

					{#if backend === 'waveshare_ble'}
						{@const gate = layerHasChannel(layer) ? Number(layer.channel) : null}
						<div class="flex flex-col gap-3 rounded-item bg-well p-3">
							<div class="flex flex-wrap items-center gap-3">
								{#if gate === null}
									<p class="text-sm text-ink-muted">
										{$_('components.servo.servoLayerCalibrator.ble.gateNone')}
									</p>
								{:else}
									<Badge tone={bleBadgeTone(gateStateByGate[gate])}>
										{bleBadgeLabel(gateStateByGate[gate])}
									</Badge>
									<div class="ml-auto flex items-center gap-2">
										<Button
											size="sm"
											loading={gateBusyByGate[gate] === 'opening'}
											disabled={Boolean(gateBusyByGate[gate])}
											onclick={() => moveBleGate(gate, 'open')}
										>
											{$_('components.servo.servoLayerCalibrator.ble.buttons.open')}
										</Button>
										<Button
											size="sm"
											loading={gateBusyByGate[gate] === 'closing'}
											disabled={Boolean(gateBusyByGate[gate])}
											onclick={() => moveBleGate(gate, 'close')}
										>
											{$_('components.servo.servoLayerCalibrator.ble.buttons.close')}
										</Button>
										<Button
											size="sm"
											loading={gateBusyByGate[gate] === 'calibrating'}
											disabled={Boolean(gateBusyByGate[gate])}
											onclick={() => calibrateBleGate(gate)}
										>
											{$_('components.servo.servoLayerCalibrator.ble.buttons.calibrate')}
										</Button>
									</div>
								{/if}
							</div>
						</div>
					{:else}
						<div class="flex flex-col gap-3 rounded-item bg-well p-3">
							<div class="flex flex-wrap items-center gap-x-8 gap-y-4">
								<div class="flex flex-col gap-1.5">
									<span class="text-sm font-medium text-ink"
										>{$_('components.servo.servoLayerCalibrator.flapLabel')}</span
									>
									<div
										role="group"
										aria-label={$_('components.servo.servoLayerCalibrator.moveFlapGroupLabel', {
											values: { label: layer.label }
										})}
										class="flex h-(--size-control-sm) items-stretch divide-x divide-line-strong overflow-hidden rounded-button border border-line-strong bg-field"
									>
										<button
											type="button"
											aria-label={$_('components.servo.servoLayerCalibrator.lowerAngleLabel')}
											disabled={idle || !canMove}
											onpointerdown={(e) => startHold(e, layer.layerIndex, -1)}
											onpointerup={stopHold}
											onpointerleave={stopHold}
											onpointercancel={stopHold}
											onclick={(e) => keyboardClick(e, layer.layerIndex, -1)}
											class="flex w-(--size-control-sm) items-center justify-center text-ink transition-colors hover:bg-hover focus-visible:-outline-offset-2 disabled:pointer-events-none disabled:opacity-45"
										>
											<ChevronLeft size={16} />
										</button>
										<span class="num flex min-w-16 items-center justify-center px-2 text-sm text-ink">
											{layer.currentAngle === null
												? $_('components.servo.servoLayerCalibrator.unknownAngle')
												: `${layer.currentAngle}°`}
										</span>
										<button
											type="button"
											aria-label={$_('components.servo.servoLayerCalibrator.higherAngleLabel')}
											disabled={idle || !canMove}
											onpointerdown={(e) => startHold(e, layer.layerIndex, 1)}
											onpointerup={stopHold}
											onpointerleave={stopHold}
											onpointercancel={stopHold}
											onclick={(e) => keyboardClick(e, layer.layerIndex, 1)}
											class="flex w-(--size-control-sm) items-center justify-center text-ink transition-colors hover:bg-hover focus-visible:-outline-offset-2 disabled:pointer-events-none disabled:opacity-45"
										>
											<ChevronRight size={16} />
										</button>
									</div>
								</div>
								{@render flapStep(layer, 'open', canMove, idle)}
								{@render flapStep(layer, 'closed', canMove, idle)}
								{#if layer.openAngle !== null || layer.closedAngle !== null}
									<Button
										variant="ghost"
										size="sm"
										icon={Eraser}
										class="ml-auto self-end"
										disabled={idle || !canMove}
										onclick={() => clearAngles(layer.layerIndex)}
									>
										{$_('components.servo.servoLayerCalibrator.startOverButton')}
									</Button>
								{/if}
							</div>

							{#if hint || step || layer.lockStatus !== 'idle'}
								<div class="flex flex-wrap items-center gap-x-3 gap-y-1.5">
									{#if hint}
										<p class="text-sm text-ink-muted">{hint}</p>
									{:else if step === 'open'}
										<p class="text-sm text-ink">
											{$_('components.servo.servoLayerCalibrator.instructions.stepOpen')}
										</p>
									{:else if step === 'closed'}
										<p class="text-sm text-ink">
											{$_('components.servo.servoLayerCalibrator.instructions.stepClosed')}
										</p>
									{/if}
									{#if layer.lockStatus === 'saving'}
										<span class="flex items-center gap-1.5 text-sm text-ink-muted"
											><Spinner size={12} />
											{$_('components.servo.servoLayerCalibrator.savingLabel')}</span
										>
									{:else if layer.lockStatus === 'saved'}
										<span class="text-sm text-success-ink"
											>{$_('components.servo.servoLayerCalibrator.savedLabel')}</span
										>
									{:else if layer.lockStatus === 'error'}
										<span class="text-sm text-danger-ink" title={layer.lockError}
											>{$_('components.servo.servoLayerCalibrator.notSavedLabel')}</span
										>
									{/if}
								</div>
							{/if}
						</div>
					{/if}
				</div>
			{/each}
		</div>

		{#snippet footer()}
			<div class="flex w-full flex-wrap items-center justify-end gap-2">
				{#if dirty}
					<Badge tone="warning"
						>{$_('components.servo.servoLayerCalibrator.badge.unsavedChanges')}</Badge
					>
				{/if}
				<span class="mr-auto"></span>
				<Button
					variant="ghost"
					icon={RotateCcw}
					disabled={loading || saving}
					onclick={loadSettings}
				>
					{$_('components.servo.servoLayerCalibrator.reloadButton')}
				</Button>
				<Button variant="primary" loading={saving} disabled={loading} onclick={saveSettings}>
					{$_('components.servo.servoLayerCalibrator.saveLayersButton')}
				</Button>
			</div>
		{/snippet}
	</Panel>

	{#if advanced && backend !== 'waveshare_ble'}
		<ServoSpeedSettings
			bind:openSpeed
			bind:closeSpeed
			bind:homingSpeed
			disabled={loading || saving}
		/>
	{/if}
{/if}

{#if errorMsg}
	<Alert tone="danger">{errorMsg}</Alert>
{:else if bleLastError}
	<Alert tone="danger">{bleLastError}</Alert>
{:else if statusMsg}
	<p class="text-sm text-ink-muted">{statusMsg}</p>
{/if}
