<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import { getMachinesContext } from '$lib/machines/context';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import SegmentedControl from '$lib/components/ui/SegmentedControl.svelte';
	import Stat from '$lib/components/ui/Stat.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import ArrowDown from '@lucide/svelte/icons/arrow-down';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import Check from '@lucide/svelte/icons/check';
	import BinLayoutViz from './BinLayoutViz.svelte';
	import ErrorBanner from './ErrorBanner.svelte';
	import { binCenterAngle, reachInfo } from '$lib/chute/geometry';

	const manager = getMachinesContext();

	const FINE = 0.25;
	const COARSE = 2;

	// ---- Canonical aiming geometry (mirrors backend chute.py) --------------
	// bin_center = θ0 + section·(360/N) + (i + 0.5)·(W / K)
	// The built-in defaults (backend hardware_settings/chute.py).
	const DEFAULT_NUM_SECTIONS = 6;
	const DEFAULT_SECTION_WIDTH_DEG = 51.75;
	const DEFAULT_FIRST_SECTION_OFFSET_DEG = 8.25;
	let numSections = $state(DEFAULT_NUM_SECTIONS);
	let sectionWidthDeg = $state(DEFAULT_SECTION_WIDTH_DEG);
	let firstSectionOffsetDeg = $state(DEFAULT_FIRST_SECTION_OFFSET_DEG);
	let maxAngleDeg = $state(350);

	const sectionPitchDeg = $derived(numSections > 0 ? 360 / numSections : 60);
	const pillarWidthDeg = $derived(sectionPitchDeg - sectionWidthDeg);

	// ---- Live status -------------------------------------------------------
	let liveAvailable = $state(false);
	let currentAngle = $state<number | null>(null);
	let stepperStopped = $state<boolean | null>(null);
	let liveEndstopTriggered = $state<boolean | null>(null);
	let stepperPositionDeg = $state<number | null>(null);
	let liveRequestInFlight = false;

	// ---- Calibration measurement -------------------------------------------
	let homed = $state(false);
	let binsInTestSection = $state(3);
	let capturedFirst = $state<number | null>(null);
	let capturedLast = $state<number | null>(null);
	let calibrationLabel = $state('');

	// ---- History -----------------------------------------------------------
	type Calibration = {
		id: string;
		created_at: number;
		label: string | null;
		num_sections: number;
		section_width_deg: number;
		first_section_offset_deg: number;
		is_active: boolean;
	};
	let calibrations = $state<Calibration[]>([]);

	// ---- UI state ----------------------------------------------------------
	let loadedMachineKey = $state('');
	let editingParams = $state(false);
	let busy = $state(false); // a chute-moving action is in flight
	let movingJog = false; // guards arrow-key repeat flooding
	let homingState = $state(false);
	let errorMsg = $state<string | null>(null);
	let statusMsg = $state('');

	// Vertical wizard gating — each step unlocks on the previous step's data.
	const activeStep = $derived(
		!homed ? 1 : capturedFirst === null ? 2 : capturedLast === null ? 3 : 4
	);
	const step2Locked = $derived(!homed);
	const step3Locked = $derived(capturedFirst === null);
	const step4Locked = $derived(capturedFirst === null || capturedLast === null);
	// The backend refuses a last bin that is not at a higher angle than the first.
	const captureOrderWrong = $derived(
		capturedFirst !== null && capturedLast !== null && capturedLast <= capturedFirst
	);

	function currentBackendBaseUrl(): string {
		return (
			machineHttpBaseUrlFromWsUrl(
				manager.selectedMachine?.status === 'connected' ? manager.selectedMachine.url : null
			) ?? getBackendHttpBase()
		);
	}

	function fmt(v: number | null | undefined, d = 1): string {
		return v === null || v === undefined || !Number.isFinite(v) ? '--' : v.toFixed(d);
	}
	function fmtDate(epoch: number): string {
		try {
			return new Date(epoch * 1000).toLocaleString();
		} catch {
			return '--';
		}
	}

	function angleFor(section: number, bin: number, binsInSection: number): number {
		return binCenterAngle(
			{ numSections, sectionWidthDeg, firstSectionOffsetDeg },
			section,
			bin,
			binsInSection
		);
	}
	function isReachable(angle: number): boolean {
		return reachInfo(angle, maxAngleDeg).reachable;
	}

	async function loadSettings() {
		errorMsg = null;
		try {
			const res = await fetch(`${currentBackendBaseUrl()}/api/hardware-config/chute`);
			if (!res.ok) throw new Error(await res.text());
			const c = await res.json();
			if (Number.isFinite(c?.num_sections)) numSections = c.num_sections;
			if (Number.isFinite(c?.section_width_deg)) sectionWidthDeg = c.section_width_deg;
			if (Number.isFinite(c?.first_section_offset_deg)) firstSectionOffsetDeg = c.first_section_offset_deg;
			if (Number.isFinite(c?.max_angle_deg)) maxAngleDeg = c.max_angle_deg;
			void loadCalibrations();
			void loadLive();
		} catch (e: any) {
			errorMsg = e.message ?? $_('routes.settings.chuteAiming.page.loadError');
		}
	}

	async function loadCalibrations() {
		try {
			const res = await fetch(`${currentBackendBaseUrl()}/api/hardware-config/chute/calibrations`);
			if (!res.ok) return;
			const p = await res.json();
			if (Array.isArray(p?.calibrations)) calibrations = p.calibrations;
		} catch {
			// non-critical
		}
	}

	async function loadLive() {
		if (liveRequestInFlight) return;
		liveRequestInFlight = true;
		try {
			const res = await fetch(`${currentBackendBaseUrl()}/api/hardware-config/chute/live`);
			if (!res.ok) throw new Error(await res.text());
			const p = await res.json();
			liveAvailable = Boolean(p?.live_available);
			currentAngle =
				typeof p?.current_angle === 'number' && Number.isFinite(p.current_angle) ? p.current_angle : null;
			stepperStopped = typeof p?.stepper_stopped === 'boolean' ? p.stepper_stopped : null;
			liveEndstopTriggered = typeof p?.endstop_triggered === 'boolean' ? p.endstop_triggered : null;
			stepperPositionDeg =
				typeof p?.stepper_position_degrees === 'number' && Number.isFinite(p.stepper_position_degrees)
					? p.stepper_position_degrees
					: null;
		} catch {
			// keep last known
		} finally {
			liveRequestInFlight = false;
		}
	}

	function applyResult(payload: any) {
		const s = payload?.settings;
		if (s) {
			if (Number.isFinite(s.num_sections)) numSections = s.num_sections;
			if (Number.isFinite(s.section_width_deg)) sectionWidthDeg = s.section_width_deg;
			if (Number.isFinite(s.first_section_offset_deg)) firstSectionOffsetDeg = s.first_section_offset_deg;
		}
		if (Array.isArray(payload?.calibrations)) calibrations = payload.calibrations;
		statusMsg = payload?.message ?? statusMsg;
	}

	async function post(path: string, body?: unknown): Promise<any> {
		const res = await fetch(`${currentBackendBaseUrl()}${path}`, {
			method: 'POST',
			headers: body ? { 'Content-Type': 'application/json' } : undefined,
			body: body ? JSON.stringify(body) : undefined
		});
		if (!res.ok) throw new Error(await res.text());
		return res.json();
	}

	async function saveParams() {
		busy = true;
		errorMsg = null;
		statusMsg = '';
		try {
			applyResult(
				await post('/api/hardware-config/chute/aiming', {
					num_sections: numSections,
					section_width_deg: sectionWidthDeg,
					first_section_offset_deg: firstSectionOffsetDeg,
					label: $_('routes.settings.chuteAiming.page.manualEditLabel')
				})
			);
			editingParams = false;
		} catch (e: any) {
			errorMsg = e.message ?? $_('routes.settings.chuteAiming.page.saveError');
		} finally {
			busy = false;
		}
	}

	async function homeChute() {
		homingState = true;
		errorMsg = null;
		statusMsg = '';
		try {
			const p = await post('/api/hardware-config/chute/calibrate/find-endstop');
			homed = true;
			statusMsg = p?.message ?? $_('routes.settings.chuteAiming.page.homedStatus');
			void loadLive();
		} catch (e: any) {
			errorMsg = e.message ?? $_('routes.settings.chuteAiming.page.homeError');
		} finally {
			homingState = false;
		}
	}

	async function cancelHome() {
		try {
			await post('/api/hardware-config/chute/calibrate/cancel');
			statusMsg = $_('routes.settings.chuteAiming.page.homingCanceledStatus');
		} catch (e: any) {
			errorMsg = e.message ?? $_('routes.settings.chuteAiming.page.cancelHomeError');
		}
	}

	async function jogTo(angle: number) {
		if (movingJog) return;
		movingJog = true;
		busy = true;
		errorMsg = null;
		try {
			const p = await post('/api/hardware-config/chute/move-to-angle', { angle });
			currentAngle = typeof p?.target_angle === 'number' ? p.target_angle : currentAngle;
			void loadLive();
		} catch (e: any) {
			errorMsg = e.message ?? $_('routes.settings.chuteAiming.page.jogError');
		} finally {
			movingJog = false;
			busy = false;
		}
	}
	function nudge(delta: number) {
		const base = currentAngle ?? 0;
		void jogTo(Math.max(0, Math.min(360, Number((base + delta).toFixed(3)))));
	}

	function captureFirst() {
		if (currentAngle !== null) capturedFirst = Number(currentAngle.toFixed(2));
	}
	function captureLast() {
		if (currentAngle !== null) capturedLast = Number(currentAngle.toFixed(2));
	}
	function resetCalibration() {
		homed = false;
		capturedFirst = null;
		capturedLast = null;
		calibrationLabel = '';
	}

	async function deriveAndLockIn() {
		if (capturedFirst === null || capturedLast === null) return;
		busy = true;
		errorMsg = null;
		statusMsg = '';
		try {
			applyResult(
				await post('/api/hardware-config/chute/aiming/derive', {
					first_bin_angle: capturedFirst,
					last_bin_angle: capturedLast,
					bins_in_test_section: binsInTestSection,
					num_sections: numSections,
					label: calibrationLabel.trim() || null
				})
			);
			resetCalibration();
		} catch (e: any) {
			errorMsg = e.message ?? $_('routes.settings.chuteAiming.page.deriveError');
		} finally {
			busy = false;
		}
	}

	async function lockIn(id: string) {
		busy = true;
		errorMsg = null;
		statusMsg = '';
		try {
			applyResult(await post(`/api/hardware-config/chute/calibrations/${id}/activate`));
		} catch (e: any) {
			errorMsg = e.message ?? $_('routes.settings.chuteAiming.page.lockInError');
		} finally {
			busy = false;
		}
	}

	async function deleteCalibration(id: string) {
		errorMsg = null;
		try {
			const res = await fetch(`${currentBackendBaseUrl()}/api/hardware-config/chute/calibrations/${id}`, {
				method: 'DELETE'
			});
			if (!res.ok) throw new Error(await res.text());
			const p = await res.json();
			if (Array.isArray(p?.calibrations)) calibrations = p.calibrations;
		} catch (e: any) {
			errorMsg = e.message ?? $_('routes.settings.chuteAiming.page.deleteError');
		}
	}

	// ---- Test aim ----------------------------------------------------------
	// One viz instance per layer size so all are visible at once, instead of a
	// single circle you toggle between 1–5 bins.
	const VIZ_SIZES = [1, 2, 3, 4, 5];
	let selected = $state<{ section: number; bin: number; binCount: number } | null>(null);

	async function testAimSelected() {
		if (!selected) return;
		busy = true;
		errorMsg = null;
		statusMsg = '';
		try {
			const p = await post('/api/hardware-config/chute/move-to-virtual-bin', {
				num_sections: numSections,
				bins_in_section: selected.binCount,
				section_index: selected.section,
				bin_index: selected.bin
			});
			statusMsg = $_('routes.settings.chuteAiming.page.testAimStatus', {
				values: {
					binCount: selected.binCount,
					section: selected.section + 1,
					bin: selected.bin + 1,
					angle: fmt(p?.target_angle)
				}
			});
			void loadLive();
		} catch (e: any) {
			errorMsg = e.message ?? $_('routes.settings.chuteAiming.page.testAimError');
		} finally {
			busy = false;
		}
	}

	// ---- Keyboard jog (only while a jog step is the active wizard step) -----
	function handleKey(e: KeyboardEvent) {
		if (activeStep !== 2 && activeStep !== 3) return;
		const el = document.activeElement;
		if (el instanceof HTMLElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) return;
		let delta = 0;
		if (e.key === 'ArrowUp') delta = FINE;
		else if (e.key === 'ArrowDown') delta = -FINE;
		else if (e.key === 'ArrowLeft') delta = -COARSE;
		else if (e.key === 'ArrowRight') delta = COARSE;
		else return;
		e.preventDefault();
		nudge(delta);
	}

	$effect(() => {
		const machineKey =
			(manager.selectedMachine?.status === 'connected' ? manager.selectedMachine.url : null) ?? '__local__';
		if (machineKey !== loadedMachineKey) {
			loadedMachineKey = machineKey;
			void loadSettings();
		}
	});

	// Poll live status; speed up while homing so you can watch the chute drive
	// to 0° and back off to the first bin. Kept modest so it never floods.
	$effect(() => {
		const period = homingState ? 200 : 600;
		const interval = setInterval(() => void loadLive(), period);
		return () => clearInterval(interval);
	});

</script>

<svelte:head><title>{$_('routes.settings.chuteAiming.page.pageTitle')}</title></svelte:head>

<svelte:window onkeydown={handleKey} />

{#snippet jogKey(icon: typeof ArrowUp, label: string, delta: number, active: boolean)}
	<Button variant="secondary" size="sm" {icon} disabled={!active || busy} onclick={() => nudge(delta)}>
		{label}
	</Button>
{/snippet}

{#snippet jogPad(active: boolean, showBins: boolean)}
	<div class="flex w-fit flex-wrap gap-x-8 gap-y-4 rounded-control bg-well p-3">
		<div class="flex flex-col gap-2" class:opacity-40={!active}>
			<span class="label">{$_('routes.settings.chuteAiming.page.jogPad.jogLabel')}</span>
			<div class="grid grid-cols-3 gap-1 select-none">
				<span></span>
				{@render jogKey(ArrowUp, `+${FINE}°`, FINE, active)}
				<span></span>
				{@render jogKey(ArrowLeft, `−${COARSE}°`, -COARSE, active)}
				<span class="flex flex-col items-center justify-center rounded-control bg-surface px-2 py-1">
					<span class="num text-sm font-medium text-ink">{fmt(currentAngle, 2)}°</span>
					<span class="text-xs text-ink-muted">{$_('routes.settings.chuteAiming.page.jogPad.now')}</span>
				</span>
				{@render jogKey(ArrowRight, `+${COARSE}°`, COARSE, active)}
				<span></span>
				{@render jogKey(ArrowDown, `−${FINE}°`, -FINE, active)}
				<span></span>
			</div>
			{#if active}
				<p class="max-w-60 text-sm text-ink-muted">
					{$_('routes.settings.chuteAiming.page.jogPad.keyboardHint', {
						values: { fine: FINE, coarse: COARSE }
					})}
				</p>
			{/if}
		</div>
		{#if showBins}
			<div class="flex flex-col gap-2">
				<span class="label">{$_('routes.settings.chuteAiming.page.jogPad.binsInSectionLabel')}</span>
				<SegmentedControl
					label={$_('routes.settings.chuteAiming.page.jogPad.binsInSectionLabel')}
					size="sm"
					value={String(binsInTestSection)}
					options={['3', '4', '5', '6'].map((n) => ({ value: n, label: n }))}
					onchange={(n) => (binsInTestSection = Number(n))}
				/>
				<p class="max-w-52 text-sm text-ink-muted">
					{$_('routes.settings.chuteAiming.page.jogPad.binsInSectionHelp')}
				</p>
			</div>
		{/if}
	</div>
{/snippet}

{#snippet stepHead(n: number, done: boolean, title: string)}
	<div class="flex items-center gap-2">
		<span
			class="flex size-6 shrink-0 items-center justify-center rounded-badge text-sm font-medium {done
				? 'bg-success-soft text-success-ink'
				: activeStep === n
					? 'bg-primary-soft text-primary-ink'
					: 'bg-well text-ink-muted'}"
		>
			{#if done}<Check size={14} />{:else}{n}{/if}
		</span>
		<h3 class="text-sm font-medium text-ink">{title}</h3>
	</div>
{/snippet}

{#snippet editFooter()}
	<Button
		variant="ghost"
		class="mr-auto"
		onclick={() => {
			numSections = DEFAULT_NUM_SECTIONS;
			sectionWidthDeg = DEFAULT_SECTION_WIDTH_DEG;
			firstSectionOffsetDeg = DEFAULT_FIRST_SECTION_OFFSET_DEG;
		}}
	>
		{$_('routes.settings.chuteAiming.page.editFooter.useDefaults')}
	</Button>
	<Button
		variant="ghost"
		onclick={() => {
			editingParams = false;
			void loadSettings();
		}}
	>
		{$_('routes.settings.chuteAiming.page.editFooter.cancel')}
	</Button>
	<Button variant="primary" loading={busy} onclick={saveParams}
		>{$_('routes.settings.chuteAiming.page.editFooter.save')}</Button
	>
{/snippet}

<div class="flex max-w-4xl flex-col gap-(--gap-panels)">
	<PageHeader
		title={$_('routes.settings.chuteAiming.page.title')}
		description={$_('routes.settings.chuteAiming.page.description')}
	/>

	{#if errorMsg}
		<ErrorBanner message={errorMsg} />
	{:else if statusMsg}
		<Alert tone="info">{statusMsg}</Alert>
	{/if}

	<!-- The active parameters come first; editing them by hand hides until hovered. -->
	<Panel
		title={$_('routes.settings.chuteAiming.page.activeParams.title')}
		flush={!editingParams}
		class="group"
		footer={editingParams ? editFooter : undefined}
	>
		{#snippet actions()}
			{#if !editingParams}
				<Button
					variant="ghost"
					size="sm"
					class="opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
					onclick={() => (editingParams = true)}
				>
					{$_('routes.settings.chuteAiming.page.activeParams.editByHand')}
				</Button>
			{/if}
		{/snippet}
		{#if !editingParams}
			<div class="grid grid-cols-2 gap-px bg-line sm:grid-cols-5">
				{#each [[$_('routes.settings.chuteAiming.page.activeParams.sections'), String(numSections)], [$_('routes.settings.chuteAiming.page.activeParams.sectionWidth'), `${fmt(sectionWidthDeg, 2)}°`], [$_('routes.settings.chuteAiming.page.activeParams.offsetFromHome'), `${fmt(firstSectionOffsetDeg, 2)}°`], [$_('routes.settings.chuteAiming.page.activeParams.pitch'), `${fmt(sectionPitchDeg, 2)}°`], [$_('routes.settings.chuteAiming.page.activeParams.pillar'), `${fmt(pillarWidthDeg, 2)}°`]] as [label, value] (label)}
					<div class="bg-surface max-sm:last:col-span-2"><Stat {label} {value} /></div>
				{/each}
			</div>
		{:else}
			<div class="flex flex-col gap-4">
				<Alert tone="warning">
					{$_('routes.settings.chuteAiming.page.activeParams.editWarning')}
				</Alert>
				<div class="grid grid-cols-1 gap-4 sm:grid-cols-3">
					<Field label={$_('routes.settings.chuteAiming.page.activeParams.sectionsFieldLabel')} for="aim-sections">
						<Input id="aim-sections" type="number" bind:value={numSections} />
					</Field>
					<Field label={$_('routes.settings.chuteAiming.page.activeParams.sectionWidthFieldLabel')} for="aim-width">
						<Input id="aim-width" type="number" unit="°" bind:value={sectionWidthDeg} />
					</Field>
					<Field label={$_('routes.settings.chuteAiming.page.activeParams.offsetFieldLabel')} for="aim-offset">
						<Input id="aim-offset" type="number" unit="°" bind:value={firstSectionOffsetDeg} />
					</Field>
				</div>
			</div>
		{/if}
	</Panel>

	<Panel
		title={$_('routes.settings.chuteAiming.page.calibration.title')}
		description={$_('routes.settings.chuteAiming.page.calibration.description')}
		flush
	>
		{#snippet actions()}
			{#if homed || capturedFirst !== null || capturedLast !== null}
				<Button variant="ghost" size="sm" onclick={resetCalibration}
					>{$_('routes.settings.chuteAiming.page.calibration.startOver')}</Button
				>
			{/if}
		{/snippet}
		<div class="px-(--pad-panel) pb-4">
			<Alert tone="warning">
				{$_('routes.settings.chuteAiming.page.calibration.travelWarning', {
					values: { deg: fmt(maxAngleDeg, 0) }
				})}
			</Alert>
		</div>
		<ol class="divide-y divide-line border-t border-line">
			<li class="flex flex-col gap-3 px-(--pad-panel) py-4">
				{@render stepHead(1, homed, $_('routes.settings.chuteAiming.page.steps.home'))}
				<p class="text-sm text-ink-muted">{$_('routes.settings.chuteAiming.page.calibration.step1.description')}</p>
				<dl class="flex w-fit flex-wrap gap-x-6 gap-y-1 rounded-control bg-well px-3 py-2 text-sm">
					<div class="flex gap-2">
						<dt class="text-ink-muted">{$_('routes.settings.chuteAiming.page.calibration.step1.chuteAngle')}</dt>
						<dd class="num font-medium text-ink">{fmt(currentAngle, 1)}°</dd>
					</div>
					<div class="flex gap-2">
						<dt class="text-ink-muted">{$_('routes.settings.chuteAiming.page.calibration.step1.motorAngle')}</dt>
						<dd class="num font-medium text-ink">{fmt(stepperPositionDeg, 1)}°</dd>
					</div>
					<div class="flex gap-2">
						<dt class="text-ink-muted">{$_('routes.settings.chuteAiming.page.calibration.step1.endstop')}</dt>
						<dd class="font-medium {liveEndstopTriggered ? 'text-success-ink' : 'text-ink'}">
							{liveEndstopTriggered === null
								? '–'
								: liveEndstopTriggered
									? $_('routes.settings.chuteAiming.page.calibration.step1.triggered')
									: $_('routes.settings.chuteAiming.page.calibration.step1.open')}
						</dd>
					</div>
				</dl>
				<div class="flex flex-wrap items-center gap-2">
					<Button variant={homed ? 'secondary' : 'primary'} loading={homingState} onclick={homeChute}>
						{homed
							? $_('routes.settings.chuteAiming.page.calibration.step1.homeAgain')
							: $_('routes.settings.chuteAiming.page.calibration.step1.homeButton')}
					</Button>
					{#if homingState}<Button variant="danger" onclick={cancelHome}
							>{$_('routes.settings.chuteAiming.page.calibration.step1.cancel')}</Button
						>{/if}
					{#if homed}<span class="text-sm text-success-ink"
							>{$_('routes.settings.chuteAiming.page.calibration.step1.homedBadge')}</span
						>{/if}
				</div>
			</li>

			<li
				class="flex flex-col gap-3 px-(--pad-panel) py-4"
				class:opacity-50={step2Locked}
				class:pointer-events-none={step2Locked}
			>
				{@render stepHead(2, capturedFirst !== null, $_('routes.settings.chuteAiming.page.steps.firstBin'))}
				<p class="text-sm text-ink-muted">
					{#if step2Locked}
						{$_('routes.settings.chuteAiming.page.calibration.step2.locked')}
					{:else}
						{$_('routes.settings.chuteAiming.page.calibration.step2.description')}
					{/if}
				</p>
				{@render jogPad(activeStep === 2, true)}
				<div class="flex flex-wrap items-center gap-3">
					<Button variant="primary" disabled={step2Locked || currentAngle === null} onclick={captureFirst}>
						{$_('routes.settings.chuteAiming.page.calibration.step2.captureButton')}
					</Button>
					<span class="num text-sm text-ink-muted"
						>{$_('routes.settings.chuteAiming.page.calibration.step2.captured', {
							values: { deg: fmt(capturedFirst, 2) }
						})}</span
					>
				</div>
			</li>

			<li
				class="flex flex-col gap-3 px-(--pad-panel) py-4"
				class:opacity-50={step3Locked}
				class:pointer-events-none={step3Locked}
			>
				{@render stepHead(3, capturedLast !== null, $_('routes.settings.chuteAiming.page.steps.lastBin'))}
				<p class="text-sm text-ink-muted">
					{#if step3Locked}
						{$_('routes.settings.chuteAiming.page.calibration.step3.locked')}
					{:else}
						{$_('routes.settings.chuteAiming.page.calibration.step3.description')}
					{/if}
				</p>
				{@render jogPad(activeStep === 3, false)}
				<div class="flex flex-wrap items-center gap-3">
					<Button variant="primary" disabled={step3Locked || currentAngle === null} onclick={captureLast}>
						{$_('routes.settings.chuteAiming.page.calibration.step3.captureButton')}
					</Button>
					<span class="num text-sm text-ink-muted"
						>{$_('routes.settings.chuteAiming.page.calibration.step3.captured', {
							values: { deg: fmt(capturedLast, 2) }
						})}</span
					>
				</div>
			</li>

			<li
				class="flex flex-col gap-3 px-(--pad-panel) py-4"
				class:opacity-50={step4Locked}
				class:pointer-events-none={step4Locked}
			>
				{@render stepHead(4, false, $_('routes.settings.chuteAiming.page.steps.lockIn'))}
				{#if captureOrderWrong}
					<p class="text-sm text-danger-ink">
						{$_('routes.settings.chuteAiming.page.calibration.step4.orderWrong')}
					</p>
				{:else if !step4Locked}
					{@const slot = (capturedLast! - capturedFirst!) / Math.max(1, binsInTestSection - 1)}
					{@const w = slot * binsInTestSection}
					<dl class="flex flex-wrap gap-x-6 gap-y-1 text-sm">
						<div class="flex gap-2"><dt class="text-ink-muted">{$_('routes.settings.chuteAiming.page.calibration.step4.sectionWidth')}</dt><dd class="num text-ink">{fmt(w, 2)}°</dd></div>
						<div class="flex gap-2"><dt class="text-ink-muted">{$_('routes.settings.chuteAiming.page.calibration.step4.offsetFromHome')}</dt><dd class="num text-ink">{fmt(capturedFirst! - 0.5 * slot, 2)}°</dd></div>
						<div class="flex gap-2"><dt class="text-ink-muted">{$_('routes.settings.chuteAiming.page.calibration.step4.pillar')}</dt><dd class="num text-ink">{fmt(sectionPitchDeg - w, 2)}°</dd></div>
					</dl>
					<Field
						label={$_('routes.settings.chuteAiming.page.calibration.step4.labelFieldLabel')}
						for="aim-label"
						help={$_('routes.settings.chuteAiming.page.calibration.step4.labelFieldHelp')}
						class="max-w-sm"
					>
						<Input
							id="aim-label"
							bind:value={calibrationLabel}
							placeholder={$_('routes.settings.chuteAiming.page.calibration.step4.labelPlaceholder')}
						/>
					</Field>
				{:else}
					<p class="text-sm text-ink-muted">{$_('routes.settings.chuteAiming.page.calibration.step4.locked')}</p>
				{/if}
				<div>
					<Button variant="primary" loading={busy} disabled={step4Locked || captureOrderWrong} onclick={deriveAndLockIn}>
						{$_('routes.settings.chuteAiming.page.calibration.step4.lockInButton')}
					</Button>
				</div>
			</li>
		</ol>
	</Panel>

	<Panel title={$_('routes.settings.chuteAiming.page.saved.title')} flush>
		{#if calibrations.length === 0}
			<p class="px-(--pad-panel) pb-4 text-sm text-ink-muted">
				{$_('routes.settings.chuteAiming.page.saved.empty')}
			</p>
		{:else}
			<ul class="divide-y divide-line">
				{#each calibrations as cal (cal.id)}
					<li class="flex items-center justify-between gap-3 px-(--pad-panel) py-(--pad-row)">
						<div class="min-w-0 text-sm">
							<div class="flex items-center gap-2">
								<span class="truncate font-medium text-ink"
									>{cal.label ?? $_('routes.settings.chuteAiming.page.saved.fallbackLabel')}</span
								>
								{#if cal.is_active}<Badge tone="success">{$_('routes.settings.chuteAiming.page.saved.activeBadge')}</Badge>{/if}
							</div>
							<div class="num text-ink-muted">
								{fmtDate(cal.created_at)} · N {cal.num_sections} · W {fmt(cal.section_width_deg, 1)}° · θ₀
								{fmt(cal.first_section_offset_deg, 1)}°
							</div>
						</div>
						{#if !cal.is_active}
							<div class="flex shrink-0 items-center gap-2">
								<Button variant="secondary" size="sm" loading={busy} onclick={() => lockIn(cal.id)}>
									{$_('routes.settings.chuteAiming.page.saved.lockIn')}
								</Button>
								<Button variant="ghost" size="sm" onclick={() => deleteCalibration(cal.id)}
									>{$_('routes.settings.chuteAiming.page.saved.delete')}</Button
								>
							</div>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	</Panel>

	<Panel
		title={$_('routes.settings.chuteAiming.page.reachable.title')}
		description={$_('routes.settings.chuteAiming.page.reachable.description', {
			values: { max: fmt(maxAngleDeg, 0), wedge: fmt(360 - maxAngleDeg, 0) }
		})}
	>
		<div class="flex flex-col gap-4">
			<p class="text-sm text-ink-muted">
				{$_('routes.settings.chuteAiming.page.reachable.explanation', {
					values: { max: fmt(maxAngleDeg, 0) }
				})}
			</p>
			<div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
				{#each VIZ_SIZES as size (size)}
					<BinLayoutViz
						{numSections}
						{sectionWidthDeg}
						{firstSectionOffsetDeg}
						{maxAngleDeg}
						binCount={size}
						liveAngleDeg={currentAngle}
						{selected}
						onSelect={(sel) => (selected = sel)}
					/>
				{/each}
			</div>
			{#if selected}
				<div class="flex flex-wrap items-center gap-3">
					<span class="text-sm text-ink">
						{$_('routes.settings.chuteAiming.page.reachable.selectedLabel', {
							values: { binCount: selected.binCount, section: selected.section + 1, bin: selected.bin + 1 }
						})}
						<span class="num text-ink-muted">{fmt(angleFor(selected.section, selected.bin, selected.binCount), 2)}°</span>
					</span>
					<Button
						variant="primary"
						loading={busy}
						disabled={!isReachable(angleFor(selected.section, selected.bin, selected.binCount))}
						onclick={testAimSelected}
					>
						{$_('routes.settings.chuteAiming.page.reachable.testAimButton')}
					</Button>
				</div>
			{:else}
				<p class="text-sm text-ink-muted">{$_('routes.settings.chuteAiming.page.reachable.prompt')}</p>
			{/if}
		</div>
	</Panel>
</div>
