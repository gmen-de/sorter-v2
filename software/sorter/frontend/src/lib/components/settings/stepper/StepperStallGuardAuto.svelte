<!--
	A motor's automatic StallGuard tuning: on or off, what normal running reads,
	and the trigger and speed floor that come from it. The backend learns and
	sets them (stallguard_tuner.py) while the motor runs; this shows it, and
	shows nothing for a motor it does not tune.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { _ } from 'svelte-i18n';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import KeyValue from '$lib/components/ui/KeyValue.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import SettingRow from '$lib/components/ui/SettingRow.svelte';
	import Switch from '$lib/components/ui/Switch.svelte';
	import { fmtTime, type AutoTuning } from '$lib/settings/stallguard';

	let {
		motor,
		label,
		base,
		auto = $bindable(false),
		onchange
	}: {
		// The motor's name in the API: 'chute'.
		motor: string;
		// What the page calls it, in a sentence.
		label: string;
		base: () => string;
		// Whether it is on, for the forms beside it.
		auto?: boolean;
		// It set the threshold or floor, or was turned on or off.
		onchange?: () => void;
	} = $props();

	const POLL_MS = 4000;

	let tuning = $state<AutoTuning | null>(null);
	let saving = $state(false);
	let error = $state<string | null>(null);

	async function readError(res: Response): Promise<string> {
		const body = await res.json().catch(() => ({}));
		return typeof body?.detail === 'string' ? body.detail : `HTTP ${res.status}`;
	}

	function take(next: AutoTuning) {
		const lastChange = tuning?.changes?.[0]?.at;
		const changed =
			tuning !== null && (next.auto !== tuning.auto || next.changes?.[0]?.at !== lastChange);
		tuning = next;
		auto = next.auto;
		if (changed) onchange?.();
	}

	async function load() {
		try {
			const res = await fetch(`${base()}/stepper/${motor}/stallguard-auto`);
			if (!res.ok) throw new Error(await readError(res));
			take(await res.json());
			error = null;
		} catch (e: any) {
			error = e.message ?? $_('components.settings.stepper.stepperStallGuardAuto.errors.readFailed');
		}
	}

	async function send(body: { enabled?: boolean; learn_again?: boolean }) {
		saving = true;
		error = null;
		try {
			const res = await fetch(`${base()}/stepper/${motor}/stallguard-auto`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(body)
			});
			if (!res.ok) throw new Error(await readError(res));
			take(await res.json());
			onchange?.();
		} catch (e: any) {
			error =
				e.message ?? $_('components.settings.stepper.stepperStallGuardAuto.errors.changeFailed');
		} finally {
			saving = false;
		}
	}

	onMount(() => {
		void load();
		const timer = setInterval(load, POLL_MS);
		return () => clearInterval(timer);
	});

	const on = $derived(!!tuning?.auto && !!tuning?.running);
	const lastChange = $derived(tuning?.changes?.[0] ?? null);
	const facts = $derived.by(() => {
		const t = tuning;
		if (!t || !on) return [];
		const base = 'components.settings.stepper.stepperStallGuardAuto';
		const items: { label: string; value: string }[] = [];
		if (t.trigger != null)
			items.push({
				label: $_(`${base}.facts.tripsAt`),
				value: $_(`${base}.facts.tripsAtValue`, { values: { trigger: t.trigger } })
			});
		items.push({
			label: $_(`${base}.facts.normalRunning`),
			value:
				t.normal_low != null
					? $_(`${base}.facts.normalRunningValue`, { values: { value: t.normal_low } })
					: $_(`${base}.facts.stillLearning`)
		});
		if (t.tcoolthrs != null)
			items.push({
				label: $_(`${base}.facts.speedFloor`),
				value:
					t.floor_speed != null
						? $_(`${base}.facts.speedFloorValueWithFloor`, {
								values: { tcoolthrs: t.tcoolthrs, floor: t.floor_speed.toLocaleString() }
							})
						: `${t.tcoolthrs}`
			});
		items.push({
			label: $_(`${base}.facts.learnedFrom`),
			value: $_(`${base}.facts.learnedFromValue`, {
				values: { count: (t.learned_from ?? 0).toLocaleString() }
			})
		});
		if (lastChange)
			items.push({
				label: $_(`${base}.facts.lastChange`),
				value: $_(`${base}.facts.lastChangeValue`, {
					values: {
						time: fmtTime(lastChange.at),
						from: lastChange.sgthrs[0] != null ? 2 * lastChange.sgthrs[0] : '–',
						to: 2 * lastChange.sgthrs[1]
					}
				})
			});
		return items;
	});
</script>

{#if tuning?.supported}
	<Panel
		title={$_('components.settings.stepper.stepperStallGuardAuto.panel.title')}
		description={$_('components.settings.stepper.stepperStallGuardAuto.panel.description', {
			values: { label }
		})}
		flush
	>
		{#snippet actions()}
			{#if !on}
				<Badge>{$_('components.settings.stepper.stepperStallGuardAuto.badge.off')}</Badge>
			{:else if tuning?.working_harder}
				<Badge tone="warning"
					>{$_('components.settings.stepper.stepperStallGuardAuto.badge.workingHarder')}</Badge
				>
			{:else if tuning?.state === 'tuned'}
				<Badge tone="success">{$_('components.settings.stepper.stepperStallGuardAuto.badge.tuned')}</Badge>
			{:else if tuning?.state === 'learning'}
				<Badge tone="primary"
					>{$_('components.settings.stepper.stepperStallGuardAuto.badge.learning', {
						values: { readings: tuning.readings ?? 0, needed: tuning.needed ?? 0 }
					})}</Badge
				>
			{:else if tuning?.state === 'waiting'}
				<Badge>{$_('components.settings.stepper.stepperStallGuardAuto.badge.pausedForRecording')}</Badge>
			{/if}
		{/snippet}
		<div class="divide-y divide-line border-t border-line">
			<SettingRow
				label={$_('components.settings.stepper.stepperStallGuardAuto.tuneAutomatically.label')}
				help={$_('components.settings.stepper.stepperStallGuardAuto.tuneAutomatically.help')}
			>
				<Switch
					checked={tuning.auto}
					label={$_('components.settings.stepper.stepperStallGuardAuto.tuneAutomatically.label')}
					disabled={saving || !tuning.running}
					onchange={(checked) => send({ enabled: checked })}
				/>
			</SettingRow>
			{#if error || !tuning.running || (on && (tuning.working_harder || tuning.reliable === false || tuning.error))}
				<div class="flex flex-col gap-3 px-(--pad-panel) py-(--pad-row)">
					{#if error}<Alert tone="danger">{error}</Alert>{/if}
					{#if !tuning.running}
						<Alert tone="info"
							>{$_('components.settings.stepper.stepperStallGuardAuto.alerts.notRunning')}</Alert
						>
					{/if}
					{#if on && tuning.working_harder}
						<Alert
							tone="warning"
							title={$_('components.settings.stepper.stepperStallGuardAuto.alerts.workingHarderTitle')}
							actions={learnAgain}
						>
							{$_('components.settings.stepper.stepperStallGuardAuto.alerts.workingHarderBody', {
								values: { baseline: tuning.baseline_low, normal: tuning.normal_low, label }
							})}
						</Alert>
					{/if}
					{#if on && tuning.reliable === false}
						<Alert
							tone="danger"
							title={$_('components.settings.stepper.stepperStallGuardAuto.alerts.closeToStallTitle')}
						>
							{$_('components.settings.stepper.stepperStallGuardAuto.alerts.closeToStallBody', {
								values: { normal: tuning.normal_low }
							})}
						</Alert>
					{/if}
					{#if on && tuning.error}<Alert tone="danger">{tuning.error}</Alert>{/if}
				</div>
			{/if}
			{#if facts.length > 0}
				<div class="px-(--pad-panel) py-1">
					<KeyValue items={facts} />
				</div>
			{/if}
		</div>
		{#snippet footer()}
			{#if on}
				<Button
					variant="ghost"
					icon={RotateCcw}
					loading={saving}
					onclick={() => send({ learn_again: true })}
				>
					{$_('components.settings.stepper.stepperStallGuardAuto.buttons.learnAgain')}
				</Button>
			{/if}
		{/snippet}
	</Panel>
{/if}

{#snippet learnAgain()}
	<Button size="sm" loading={saving} onclick={() => send({ learn_again: true })}
		>{$_('components.settings.stepper.stepperStallGuardAuto.buttons.learnAgain')}</Button
	>
{/snippet}
