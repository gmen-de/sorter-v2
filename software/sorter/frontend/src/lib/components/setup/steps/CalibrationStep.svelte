<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import { getMachinesContext } from '$lib/machines/context';
	import { onMount } from 'svelte';
	import Cable from '@lucide/svelte/icons/cable';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import ChuteEncoderPanel from '$lib/components/setup/ChuteEncoderPanel.svelte';

	type ChuteLiveStatus = {
		live_available: boolean;
		chute_position_sensor?: 'endstop' | 'encoder' | 'encoder_native';
		endstop_triggered: boolean | null;
		raw_endstop_high: boolean | null;
		endstop_active_high: boolean | null;
		endstop_error?: string;
		encoder_count?: number | null;
		encoder_index_synced?: boolean | null;
		encoder_error?: string;
		stepper_direction_inverted: boolean | null;
		current_angle: number | null;
		stepper_position_degrees: number | null;
		stepper_microsteps: number | null;
		stepper_stopped: boolean | null;
		homed?: boolean | null;
		digital_inputs: Array<{ channel: number; raw_high: boolean }>;
		home_pin_channel: number | null;
	};

	let { onInitialize }: { onInitialize: () => void } = $props();

	const manager = getMachinesContext();

	const SKR_PICO_WIRING_DIAGRAM_URL = '/setup/skr-pico-v1.0-headers.png';

	let showEndstopWiringHelp = $state(false);
	let loadedMachineKey = $state('');
	const systemState = $derived(manager.selectedMachine?.systemStatus?.hardware_state ?? 'standby');
	const homingStep = $derived(manager.selectedMachine?.systemStatus?.homing_step ?? null);
	let chuteLoading = $state(false);
	let chuteSaving = $state(false);
	let chuteHoming = $state(false);
	let chuteCanceling = $state(false);
	let chuteError = $state<string | null>(null);
	let chuteStatus = $state('');
	let chuteFirstBinCenter = $state(8.25);
	let chutePillarWidthDeg = $state(8.25);
	let chuteEndstopActiveHigh = $state(true);
	let chuteEncoderCountsPerRevolution = $state(144);
	let chuteLive = $state<ChuteLiveStatus>({
		live_available: false,
		endstop_triggered: null,
		raw_endstop_high: null,
		endstop_active_high: null,
		encoder_count: null,
		encoder_index_synced: null,
		stepper_direction_inverted: null,
		current_angle: null,
		stepper_position_degrees: null,
		stepper_microsteps: null,
		stepper_stopped: null,
		homed: null,
		digital_inputs: [],
		home_pin_channel: null
	});
	const chuteSensorMode = $derived(chuteLive.chute_position_sensor ?? 'endstop');
	// "encoder" and "encoder_native" are the same magnet-ring hardware, just a
	// different backend routing detail (see subsystems/distribution/chute.py) -
	// not something this calibration UI needs to distinguish.
	const chuteHasEncoder = $derived(chuteSensorMode === 'encoder' || chuteSensorMode === 'encoder_native');

	function currentBackendBaseUrl(): string {
		return (
			machineHttpBaseUrlFromWsUrl(
				manager.selectedMachine?.status === 'connected' ? manager.selectedMachine.url : null
			) ?? getBackendHttpBase()
		);
	}

	function optionalChannel(value: unknown): number | null {
		return typeof value === 'number' && Number.isInteger(value) ? value : null;
	}

	function endstopStatusLabel(
		triggered: boolean | null,
		error: string | undefined,
		liveAvailable: boolean
	): string {
		if (error) return $_('components.setup.steps.calibrationStep.endstop.readError');
		if (!liveAvailable) return $_('components.setup.steps.calibrationStep.endstop.offline');
		if (triggered === null) return '--';
		return triggered
			? $_('components.setup.steps.calibrationStep.endstop.triggered')
			: $_('components.setup.steps.calibrationStep.endstop.notTriggered');
	}

	function endstopStatusClass(triggered: boolean | null, error: string | undefined): string {
		if (error) return 'bg-danger-soft text-danger-ink';
		if (triggered) return 'bg-success-soft text-success-ink';
		return 'bg-well text-ink-muted';
	}

	async function loadChuteSettings() {
		chuteLoading = true;
		try {
			const [configRes, liveRes] = await Promise.all([
				fetch(`${currentBackendBaseUrl()}/api/hardware-config/chute`),
				fetch(`${currentBackendBaseUrl()}/api/hardware-config/chute/live`)
			]);
			let configuredHomePinChannel: number | null = null;
			if (configRes.ok) {
				const configPayload = await configRes.json();
				chuteFirstBinCenter = Number(configPayload?.first_bin_center ?? 8.25);
				chutePillarWidthDeg = Number(configPayload?.pillar_width_deg ?? 8.25);
				chuteEndstopActiveHigh = Boolean(configPayload?.endstop_active_high ?? true);
				chuteEncoderCountsPerRevolution = Number(
					configPayload?.encoder_counts_per_revolution ?? 144
				);
				configuredHomePinChannel = optionalChannel(configPayload?.home_pin_channel);
			}
			if (liveRes.ok) {
				const livePayload = (await liveRes.json()) as ChuteLiveStatus;
				chuteLive = {
					...livePayload,
					home_pin_channel: livePayload.home_pin_channel ?? configuredHomePinChannel
				};
				if (typeof livePayload.endstop_active_high === 'boolean') {
					chuteEndstopActiveHigh = livePayload.endstop_active_high;
				}
			} else {
				chuteLive = { ...chuteLive, home_pin_channel: configuredHomePinChannel };
			}
		} finally {
			chuteLoading = false;
		}
	}

	async function flipChutePolarity() {
		chuteEndstopActiveHigh = !chuteEndstopActiveHigh;
		await saveChuteSettings();
	}

	async function flipChuteDirection() {
		chuteSaving = true;
		chuteError = null;
		chuteStatus = '';
		const current = chuteLive.stepper_direction_inverted ?? false;
		try {
			const res = await fetch(
				`${currentBackendBaseUrl()}/api/setup-wizard/stepper-directions/chute`,
				{
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({ inverted: !current })
				}
			);
			if (!res.ok) throw new Error(await res.text());
			chuteStatus = $_('components.setup.steps.calibrationStep.status.directionSet', {
				values: {
					state: !current
						? $_('components.setup.steps.calibrationStep.direction.inverted')
						: $_('components.setup.steps.calibrationStep.direction.normal')
				}
			});
			await loadChuteSettings();
		} catch (e: any) {
			chuteError = e.message ?? $_('components.setup.steps.calibrationStep.errors.flipDirectionFailed');
		} finally {
			chuteSaving = false;
		}
	}

	async function saveChuteSettings() {
		chuteSaving = true;
		chuteError = null;
		chuteStatus = '';
		try {
			const res = await fetch(`${currentBackendBaseUrl()}/api/hardware-config/chute`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					first_bin_center: chuteFirstBinCenter,
					pillar_width_deg: chutePillarWidthDeg,
					endstop_active_high: chuteEndstopActiveHigh
				})
			});
			if (!res.ok) throw new Error(await res.text());
			const payload = await res.json();
			chuteStatus =
				payload?.message ?? $_('components.setup.steps.calibrationStep.status.chuteSettingsSaved');
			await loadChuteSettings();
		} catch (e: any) {
			chuteError = e.message ?? $_('components.setup.steps.calibrationStep.errors.saveSettingsFailed');
		} finally {
			chuteSaving = false;
		}
	}

	async function findChuteEndstop() {
		chuteHoming = true;
		chuteError = null;
		chuteStatus = '';
		try {
			const res = await fetch(
				`${currentBackendBaseUrl()}/api/hardware-config/chute/calibrate/find-endstop`,
				{ method: 'POST' }
			);
			if (!res.ok) throw new Error(await res.text());
			const payload = await res.json();
			chuteStatus =
				payload?.message ?? $_('components.setup.steps.calibrationStep.status.chuteReferenceFound');
			await loadChuteSettings();
		} catch (e: any) {
			chuteError =
				e.message ?? $_('components.setup.steps.calibrationStep.errors.findReferenceFailed');
		} finally {
			chuteHoming = false;
		}
	}

	async function saveEncoderCountsPerRevolution(value: number) {
		chuteEncoderCountsPerRevolution = value;
		chuteSaving = true;
		chuteError = null;
		chuteStatus = '';
		try {
			const res = await fetch(`${currentBackendBaseUrl()}/api/hardware-config/chute`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ encoder_counts_per_revolution: Math.round(value) })
			});
			if (!res.ok) throw new Error(await res.text());
			const payload = await res.json();
			chuteStatus =
				payload?.message ?? $_('components.setup.steps.calibrationStep.status.chuteSettingsSaved');
			await loadChuteSettings();
		} catch (e: any) {
			chuteError =
				e.message ?? $_('components.setup.steps.calibrationStep.errors.saveEncoderCalibrationFailed');
		} finally {
			chuteSaving = false;
		}
	}

	async function cancelChute() {
		chuteCanceling = true;
		chuteError = null;
		chuteStatus = '';
		try {
			const res = await fetch(
				`${currentBackendBaseUrl()}/api/hardware-config/chute/calibrate/cancel`,
				{ method: 'POST' }
			);
			if (!res.ok) throw new Error(await res.text());
			const payload = await res.json();
			chuteStatus =
				payload?.message ?? $_('components.setup.steps.calibrationStep.status.chuteMotionCanceled');
			await loadChuteSettings();
		} catch (e: any) {
			chuteError = e.message ?? $_('components.setup.steps.calibrationStep.errors.cancelMotionFailed');
		} finally {
			chuteCanceling = false;
		}
	}

	export async function persistPendingSettings(): Promise<boolean> {
		try {
			await saveChuteSettings();
			return chuteError === null;
		} catch {
			return false;
		}
	}

	$effect(() => {
		const machineKey =
			(manager.selectedMachine?.status === 'connected' ? manager.selectedMachine.url : null) ??
			'__local__';
		if (machineKey === loadedMachineKey) return;
		loadedMachineKey = machineKey;
		void loadChuteSettings();
	});

	onMount(() => {
		const interval = setInterval(() => {
			if (systemState === 'ready' || systemState === 'initialized') {
				void loadChuteSettings();
			}
		}, 1200);
		return () => clearInterval(interval);
	});
</script>

<div class="flex flex-col gap-(--gap-panels)">
	{#if systemState === 'initializing' || systemState === 'homing'}
		<Alert tone="warning" title={$_('components.setup.steps.calibrationStep.poweringOn.title')}>
			{homingStep ?? $_('components.setup.steps.calibrationStep.poweringOn.fallbackStep')}.
			{$_('components.setup.steps.calibrationStep.poweringOn.unlockHint')}
		</Alert>
	{:else if systemState === 'standby'}
		<Alert tone="info" title={$_('components.setup.steps.calibrationStep.steppersOff.title')}>
			{#snippet actions()}
				<Button size="sm" onclick={onInitialize}
					>{$_('components.setup.steps.calibrationStep.steppersOff.powerOnButton')}</Button
				>
			{/snippet}
		</Alert>
	{:else if systemState === 'error'}
		<Alert tone="danger" title={$_('components.setup.steps.calibrationStep.connectionFailed.title')}>
			{homingStep ?? $_('components.setup.steps.calibrationStep.connectionFailed.fallbackMessage')}
		</Alert>
	{/if}

	{#if chuteHasEncoder}
		<ChuteEncoderPanel
			connected={chuteLive.live_available}
			encoderCount={chuteLive.encoder_count ?? null}
			indexSynced={chuteLive.encoder_index_synced ?? null}
			homed={chuteLive.homed ?? null}
			bind:countsPerRevolution={chuteEncoderCountsPerRevolution}
			homing={chuteHoming}
			canceling={chuteCanceling}
			error={chuteError}
			status={chuteStatus}
			onFindIndex={findChuteEndstop}
			onCancel={cancelChute}
			onSaveCountsPerRevolution={saveEncoderCountsPerRevolution}
		/>
	{:else}
		<Panel
			title={$_('components.setup.steps.calibrationStep.endstopPanel.title')}
			description={$_('components.setup.steps.calibrationStep.endstopPanel.description')}
		>
			{#snippet actions()}
				<Button
					size="sm"
					variant="ghost"
					icon={Cable}
					onclick={() => (showEndstopWiringHelp = !showEndstopWiringHelp)}
				>
					<span class="max-sm:sr-only"
						>{showEndstopWiringHelp
							? $_('components.setup.steps.calibrationStep.wiring.hide')
							: $_('components.setup.steps.calibrationStep.wiring.show')}</span
					>
				</Button>
			{/snippet}
			<div class="flex flex-col gap-4">
				{#if showEndstopWiringHelp}
					<div
						class="grid grid-cols-1 gap-4 rounded-control bg-well p-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]"
					>
						<a
							href={SKR_PICO_WIRING_DIAGRAM_URL}
							target="_blank"
							rel="noopener noreferrer"
							class="block"
						>
							<img
								src={SKR_PICO_WIRING_DIAGRAM_URL}
								alt={$_('components.setup.steps.calibrationStep.wiringDiagram.imageAlt')}
								loading="lazy"
								class="block h-auto w-full rounded-control"
							/>
						</a>
						<div class="flex flex-col gap-2 text-sm">
							<h3 class="font-medium text-ink">
								{$_('components.setup.steps.calibrationStep.wiringDiagram.heading')}
							</h3>
							<p class="text-ink-muted">
								{$_('components.setup.steps.calibrationStep.wiringDiagram.description')}
							</p>
							<p>
								<span class="text-ink-muted"
									>{$_('components.setup.steps.calibrationStep.wiringDiagram.chuteEndstopLabel')}</span
								>
								<span class="font-mono text-ink">Distributor · E0-STOP</span>
							</p>
						</div>
					</div>
				{/if}

				<p class="text-sm text-ink-muted">
					{$_('components.setup.steps.calibrationStep.triggerHint')}
				</p>
				<div
					class="flex items-center justify-between rounded-control px-4 py-3 transition-colors {endstopStatusClass(
						chuteLive.endstop_triggered,
						chuteLive.endstop_error
					)}"
				>
					<span class="text-sm">{$_('components.setup.steps.calibrationStep.chuteEndstopLabel')}</span>
					<span class="text-base font-semibold">
						{endstopStatusLabel(
							chuteLive.endstop_triggered,
							chuteLive.endstop_error,
							chuteLive.live_available
						)}
					</span>
				</div>
				<p class="text-sm text-ink-muted">
					{$_('components.setup.steps.calibrationStep.inputChannelLabel')}
					<span class="font-medium text-ink">{chuteLive.home_pin_channel ?? '–'}</span>
					{#if chuteLive.raw_endstop_high !== null}
						· {$_('components.setup.steps.calibrationStep.rawSignalLabel')}
						<span class="font-medium text-ink"
							>{chuteLive.raw_endstop_high
								? $_('components.setup.steps.calibrationStep.signal.high')
								: $_('components.setup.steps.calibrationStep.signal.low')}</span
						>
					{/if}
				</p>
				{#if chuteLive.endstop_error}
					<Alert tone="danger"
						>{$_('components.setup.steps.calibrationStep.liveReadFailed', {
							values: { error: chuteLive.endstop_error }
						})}</Alert
					>
				{/if}

				<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
					<Field
						label={$_('components.setup.steps.calibrationStep.firstBinCenter.label')}
						for="setup-first-bin"
						help={$_('components.setup.steps.calibrationStep.savedWhenContinueHelp')}
					>
						<Input id="setup-first-bin" type="number" step={0.1} bind:value={chuteFirstBinCenter} />
					</Field>
					<Field
						label={$_('components.setup.steps.calibrationStep.pillarWidth.label')}
						for="setup-pillar"
						help={$_('components.setup.steps.calibrationStep.savedWhenContinueHelp')}
					>
						<Input
							id="setup-pillar"
							type="number"
							step={0.1}
							unit="°"
							bind:value={chutePillarWidthDeg}
						/>
					</Field>
				</div>

				<div class="flex flex-col gap-3 text-sm text-ink-muted">
					<div class="flex flex-wrap items-center gap-x-3 gap-y-1">
						<Button size="sm" loading={chuteSaving} onclick={flipChutePolarity}
							>{$_('components.setup.steps.calibrationStep.buttons.flipPolarity')}</Button
						>
						<span>
							{$_('components.setup.steps.calibrationStep.polarityHint')}
							<span class="font-medium text-ink"
								>{chuteEndstopActiveHigh
									? $_('components.setup.steps.calibrationStep.polarity.activeHigh')
									: $_('components.setup.steps.calibrationStep.polarity.activeLow')}</span
							>.
						</span>
					</div>
					<div class="flex flex-wrap items-center gap-x-3 gap-y-1">
						<Button size="sm" loading={chuteSaving} onclick={flipChuteDirection}
							>{$_('components.setup.steps.calibrationStep.buttons.flipDirection')}</Button
						>
						<span>
							{$_('components.setup.steps.calibrationStep.directionHint')}
							<span class="font-medium text-ink">
								{chuteLive.stepper_direction_inverted === null
									? '–'
									: chuteLive.stepper_direction_inverted
										? $_('components.setup.steps.calibrationStep.direction.inverted')
										: $_('components.setup.steps.calibrationStep.direction.normal')}</span
							>.
						</span>
					</div>
				</div>

				{#if chuteError}
					<Alert tone="danger">{chuteError}</Alert>
				{:else if chuteStatus}
					<Alert tone="success">{chuteStatus}</Alert>
				{/if}
			</div>
			{#snippet footer()}
				<Button variant="ghost" loading={chuteCanceling} onclick={cancelChute}
					>{$_('components.setup.steps.calibrationStep.buttons.stopMotion')}</Button
				>
				<Button
					variant="primary"
					loading={chuteHoming}
					disabled={!(systemState === 'ready' || systemState === 'initialized')}
					onclick={findChuteEndstop}
				>
					{$_('components.setup.steps.calibrationStep.buttons.findChuteEndstop')}
				</Button>
			{/snippet}
		</Panel>
	{/if}
</div>
