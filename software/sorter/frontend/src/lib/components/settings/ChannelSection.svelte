<!--
	A channel's page: its camera's picture with the channel's zones on it, the
	zones beside it (found in the picture, adjusted by hand, locked, learned
	while running), the picture, detection and LED settings, and its motor
	under the camera. In the setup wizard it opens on the zones.
-->
<script lang="ts">
	import Bug from '@lucide/svelte/icons/bug';
	import Camera from '@lucide/svelte/icons/camera';
	import Lightbulb from '@lucide/svelte/icons/lightbulb';
	import Plus from '@lucide/svelte/icons/plus';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';
	import X from '@lucide/svelte/icons/x';
	import { onMount } from 'svelte';
	import { _ } from 'svelte-i18n';
	import { getBackendHttpBase } from '$lib/backend';
	import CameraPicture from '$lib/components/CameraPicture.svelte';
	import StreamControlsOverlay from '$lib/components/StreamControlsOverlay.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import CameraPicker from '$lib/components/settings/CameraPicker.svelte';
	import ChannelLedSection from '$lib/components/settings/ChannelLedSection.svelte';
	import DetectionSettingsSidebar from '$lib/components/settings/DetectionSettingsSidebar.svelte';
	import PictureSettingsSidebar from '$lib/components/settings/PictureSettingsSidebar.svelte';
	import StepperPanel from '$lib/components/settings/StepperPanel.svelte';
	import ZoneOverlay from '$lib/components/zones/ZoneOverlay.svelte';
	import ZonePanel from '$lib/components/zones/ZonePanel.svelte';
	import { loadCameraAssignments, noCameras, type CameraSource } from '$lib/settings/cameras';
	import { pictureSettingsEqual, type PictureSettings } from '$lib/settings/picture-settings';
	import type { CameraRole, EndstopConfig, StepperKey, ZoneChannelId } from '$lib/settings/stations';
	import { roleView } from '$lib/video';
	import { defaultZones, findZones, loadPieceWatch, loadZones, saveZones } from '$lib/zones/api';
	import { sameZones, type ChannelZones, type Part } from '$lib/zones/model';

	let {
		channel,
		stepperKey = undefined,
		stepperEndstop = undefined,
		stepperLabel = undefined,
		stepperGearRatio = undefined,
		wizard = false,
		onsaved
	}: {
		channel: ZoneChannelId;
		stepperKey?: StepperKey;
		stepperEndstop?: EndstopConfig;
		stepperLabel?: string;
		stepperGearRatio?: number;
		// In the setup wizard: the zones open for editing, and saving says so.
		wizard?: boolean;
		onsaved?: () => void;
	} = $props();

	const LABEL: Record<ZoneChannelId, string> = {
		2: $_('components.settings.channelSection.label.cChannel2'),
		3: $_('components.settings.channelSection.label.cChannel3'),
		4: $_('components.settings.channelSection.label.classificationChannel')
	};
	const ROLE: Record<ZoneChannelId, CameraRole> = {
		2: 'c_channel_2',
		3: 'c_channel_3',
		4: 'classification_channel'
	};
	const DETECTION: Record<ZoneChannelId, { scope: 'feeder' | 'carousel'; camera: 'c_channel_2' | 'c_channel_3' | 'carousel' }> = {
		2: { scope: 'feeder', camera: 'c_channel_2' },
		3: { scope: 'feeder', camera: 'c_channel_3' },
		4: { scope: 'carousel', camera: 'carousel' }
	};

	const label = $derived(LABEL[channel]);
	const role = $derived(ROLE[channel]);

	let assignments = $state<Record<CameraRole, CameraSource>>(noCameras());
	let camerasLoaded = $state(false);
	let pickerOpen = $state(false);
	const source = $derived(assignments[role]);

	type Sidebar = 'picture' | 'detection' | 'led' | null;
	let sidebar = $state<Sidebar>(null);
	let picturePreview = $state<{ saved: PictureSettings; draft: PictureSettings } | null>(null);
	let showBoxes = $state(true);
	let showZones = $state(true);

	let saved = $state<ChannelZones | null>(null);
	let draft = $state<ChannelZones | null>(null);
	let editing = $state(false);
	let selected = $state<Part | null>(null);
	let finding = $state(false);
	let saving = $state(false);
	let note = $state('');
	let watchNote = $state('');
	let status = $state('');

	const zones = $derived(editing ? draft : saved);
	const dirty = $derived(editing && !sameZones(draft, saved));

	async function refreshZones() {
		try {
			saved = (await loadZones())[channel] ?? null;
		} catch (e: any) {
			note = $_('components.settings.channelSection.zonesReadError', { values: { error: e.message } });
		}
	}

	async function refreshWatch() {
		try {
			const watch = await loadPieceWatch();
			const entry = watch.channels?.[String(channel)];
			if (!watch.running || !entry?.handoffs || !entry.landing_delay_s) {
				watchNote = '';
				return;
			}
			watchNote = $_('components.settings.channelSection.watchNote', {
				values: { handoffs: entry.handoffs, seconds: entry.landing_delay_s.p50.toFixed(1) }
			});
		} catch {
			watchNote = '';
		}
	}

	async function edit() {
		if (source === null) {
			status = $_('components.settings.channelSection.chooseCameraFirst');
			return;
		}
		closeSidebar();
		try {
			draft = saved ?? (await defaultZones(channel));
			editing = true;
			note = saved ? '' : $_('components.settings.channelSection.noZonesYet');
		} catch (e: any) {
			note = $_('components.settings.channelSection.zonesOpenError', { values: { error: e.message } });
		}
	}

	async function find() {
		finding = true;
		note = '';
		closeSidebar();
		try {
			const found = await findZones(channel);
			draft = found.zones;
			editing = true;
			const pictures =
				found.report.frames === 1
					? $_('components.settings.channelSection.onePicture')
					: $_('components.settings.channelSection.picturesCount', { values: { count: found.report.frames } });
			const notes = found.report.notes.length ? ` (${found.report.notes.join('; ')})` : '';
			note = $_('components.settings.channelSection.foundRing', { values: { pictures, notes } });
		} catch (e: any) {
			note = $_('components.settings.channelSection.zonesFindError', { values: { error: e.message } });
		} finally {
			finding = false;
		}
	}

	function cancel() {
		draft = null;
		editing = false;
		selected = null;
		note = '';
	}

	async function save(next: ChannelZones | null = draft) {
		if (!next) return;
		saving = true;
		try {
			saved = await saveZones(next);
			draft = null;
			editing = wizard;
			if (wizard) draft = saved;
			selected = null;
			note = $_('components.settings.channelSection.zonesSaved');
			onsaved?.();
		} catch (e: any) {
			note = $_('components.settings.channelSection.zonesSaveError', { values: { error: e.message } });
		} finally {
			saving = false;
		}
	}

	function change(next: ChannelZones) {
		if (editing) {
			draft = next;
			return;
		}
		// Out of editing only a lock changes, and it is kept at once.
		void save(next);
	}

	function toggle(next: Sidebar) {
		if (sidebar === 'picture') picturePreview = null;
		sidebar = sidebar === next ? null : next;
	}

	function closeSidebar() {
		if (sidebar === 'picture') picturePreview = null;
		sidebar = null;
	}

	// The Picture panel's unsaved turn or flip, shown on the picture (and the
	// zones on it) before it is saved.
	const previewTransform = $derived.by(() => {
		if (!picturePreview || pictureSettingsEqual(picturePreview.saved, picturePreview.draft)) return '';
		const m = (s: PictureSettings): [number, number, number, number] => {
			const rot: Record<number, [number, number, number, number]> = {
				0: [1, 0, 0, 1],
				90: [0, -1, 1, 0],
				180: [-1, 0, 0, -1],
				270: [0, 1, -1, 0]
			};
			let out = rot[s.rotation] ?? rot[0];
			const mul = (a: number[], b: number[]): [number, number, number, number] => [
				a[0] * b[0] + a[1] * b[2],
				a[0] * b[1] + a[1] * b[3],
				a[2] * b[0] + a[3] * b[2],
				a[2] * b[1] + a[3] * b[3]
			];
			if (s.flip_horizontal) out = mul([-1, 0, 0, 1], out);
			if (s.flip_vertical) out = mul([1, 0, 0, -1], out);
			return out;
		};
		const draftM = m(picturePreview.draft);
		const savedM = m(picturePreview.saved);
		const inv = [savedM[0], savedM[2], savedM[1], savedM[3]];
		const r = [
			draftM[0] * inv[0] + draftM[1] * inv[2],
			draftM[0] * inv[1] + draftM[1] * inv[3],
			draftM[2] * inv[0] + draftM[3] * inv[2],
			draftM[2] * inv[1] + draftM[3] * inv[3]
		];
		if (r[0] === 1 && r[1] === 0 && r[2] === 0 && r[3] === 1) return '';
		return `transform: matrix(${r[0]}, ${r[2]}, ${r[1]}, ${r[3]}, 0, 0); transform-origin: center center;`;
	});

	onMount(() => {
		void loadCameraAssignments()
			.then((a) => (assignments = a))
			.finally(() => (camerasLoaded = true));
		void refreshZones().then(() => {
			if (wizard) void edit();
		});
		void refreshWatch();
		const timer = setInterval(() => void refreshWatch(), 15000);
		return () => clearInterval(timer);
	});
</script>

<div class="flex flex-col gap-(--gap-panels)">
	<div class="flex flex-wrap items-center gap-x-4 gap-y-3">
		<span class="flex min-w-0 flex-1 items-center gap-1.5 text-sm text-ink-muted">
			{$_('components.settings.channelSection.camera')}
			<span class="truncate text-ink">
				{#if !camerasLoaded}{$_('components.settings.channelSection.cameraLoading')}{:else if source === null}{$_('components.settings.channelSection.cameraNoneYet')}{:else if typeof source === 'number'}{$_('components.settings.channelSection.cameraNumber', { values: { number: source } })}{:else}{source}{/if}
			</span>
			{#if camerasLoaded && source === null}
				<Button
					variant="ghost"
					size="sm"
					icon={Plus}
					label={$_('components.settings.channelSection.chooseCamera')}
					onclick={() => (pickerOpen = true)}
				/>
			{/if}
			{#if status}<span class="ml-2 truncate">{status}</span>{/if}
		</span>
		{#if !wizard}
			<div class="flex flex-wrap items-center gap-2">
				<Button icon={Camera} disabled={editing} onclick={() => (pickerOpen = true)}>
					{$_('components.settings.channelSection.changeCamera')}
				</Button>
				<Button
					icon={SlidersHorizontal}
					disabled={editing}
					aria-pressed={sidebar === 'picture'}
					onclick={() => toggle('picture')}
				>
					{$_('components.settings.channelSection.picture')}
				</Button>
				<Button icon={Bug} disabled={editing} aria-pressed={sidebar === 'detection'} onclick={() => toggle('detection')}>
					{$_('components.settings.channelSection.detection')}
				</Button>
				<Button icon={Lightbulb} disabled={editing} aria-pressed={sidebar === 'led'} onclick={() => toggle('led')}>
					{$_('components.settings.channelSection.led')}
				</Button>
			</div>
		{/if}
	</div>

	<div class="grid gap-(--gap-panels) xl:grid-cols-[minmax(0,1fr)_25rem] xl:items-start">
		<div class="flex min-w-0 flex-col gap-(--gap-panels)">
			<!-- The picture, on the media plane: dark in both modes. -->
			<section class="dark relative overflow-hidden rounded-panel bg-media text-ink" aria-label={label}>
				<div class="relative aspect-video">
					{#if !camerasLoaded}
						<div class="absolute inset-0 flex items-center justify-center gap-2 text-sm text-ink-muted">
							<Spinner size={16} />
							{$_('components.settings.channelSection.loadingCameraFor', { values: { label } })}
						</div>
					{:else if source !== null}
						<CameraPicture
							view={roleView(role)}
							baseUrl={getBackendHttpBase()}
							alt={label}
							boxes={showBoxes}
							zones={showZones && !editing}
							class="absolute inset-0 h-full w-full"
							style={previewTransform}
						/>
						{#if editing && draft}
							<ZoneOverlay
								zones={draft}
								editing
								{selected}
								class="absolute inset-0"
								style={previewTransform}
								onchange={(next) => (draft = next)}
								onselect={(part) => (selected = part)}
							/>
						{:else}
							<div class="absolute top-2 right-2 z-10">
								<StreamControlsOverlay bind:annotated={showBoxes} bind:zones={showZones} showAnnotations showZones />
							</div>
						{/if}
					{:else}
						<div class="absolute inset-0 flex items-center justify-center px-6 text-center text-sm text-ink-muted">
							{$_('components.settings.channelSection.noCameraSetFor', { values: { label } })}
						</div>
					{/if}
				</div>
			</section>

			{#if wizard}
				<div class="grid grid-cols-1 gap-4 rounded-control bg-well p-4 text-sm text-ink-muted lg:grid-cols-[12rem_minmax(0,1fr)] lg:items-start">
					<img
						src="/setup/zone-placement-reference.png"
						alt={$_('components.settings.channelSection.wizard.imageAlt')}
						class="h-auto w-full rounded-control object-contain"
					/>
					<div class="flex flex-col gap-2">
						<h4 class="font-medium text-ink">{$_('components.settings.channelSection.wizard.heading')}</h4>
						<p>
							{$_('components.settings.channelSection.wizard.paragraph1')}
						</p>
						<p>
							{$_('components.settings.channelSection.wizard.paragraph2')}
						</p>
					</div>
				</div>
			{/if}

			{#if !wizard && stepperKey}
				<!-- The motor's panel, under the camera and as wide as it. -->
				<StepperPanel
					{stepperKey}
					endstop={stepperEndstop}
					label={stepperLabel}
					gearRatioOverride={stepperGearRatio}
					keyboardShortcuts={true}
				/>
			{/if}
		</div>

		<div class="flex min-w-0 flex-col gap-(--gap-panels)">
			{#if sidebar === 'picture'}
				{#key `${role}::${source === null ? 'none' : String(source)}`}
					<PictureSettingsSidebar
						{role}
						{label}
						{source}
						hasCamera={source !== null}
						onPreviewChange={(_role, savedSettings, draftSettings) =>
							(picturePreview = { saved: savedSettings, draft: draftSettings })}
						onClose={closeSidebar}
						onSaved={() => {
							closeSidebar();
							status = 'Picture settings saved.';
						}}
					/>
				{/key}
			{:else if sidebar === 'detection'}
				<DetectionSettingsSidebar
					scope={DETECTION[channel].scope}
					camera={DETECTION[channel].camera}
					{label}
					onClose={closeSidebar}
				/>
			{:else if sidebar === 'led'}
				<Panel
					title={$_('components.settings.channelSection.led')}
					description={$_('components.settings.channelSection.ledPanelDescription', { values: { label } })}
				>
					{#snippet actions()}
						<Button
							variant="ghost"
							size="sm"
							icon={X}
							label={$_('components.settings.channelSection.closeLedSettings')}
							onclick={closeSidebar}
						/>
					{/snippet}
					<ChannelLedSection channelKey={role} />
				</Panel>
			{:else}
				<ZonePanel
					{zones}
					{editing}
					{dirty}
					{finding}
					{saving}
					{selected}
					{note}
					watch={watchNote}
					onchange={change}
					onselect={(part) => (selected = part)}
					onfind={find}
					onedit={edit}
					oncancel={cancel}
					onsave={() => save()}
				/>
			{/if}
		</div>
	</div>
</div>

<CameraPicker bind:open={pickerOpen} {role} {label} bind:assignments onassigned={(message) => (status = message)} />
