<script lang="ts">
	import ChevronLeft from '@lucide/svelte/icons/chevron-left';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Square from '@lucide/svelte/icons/square';
	import { _ } from 'svelte-i18n';
	import SegmentedControl from '$lib/components/ui/SegmentedControl.svelte';
	import Input from '$lib/components/ui/Input.svelte';

	const MAX_PULSE_DURATION_S = 120;

	type PulseMode = 'duration' | 'degrees';

	let {
		stepperKey,
		keyboardShortcuts,
		pulsing,
		homing,
		canceling,
		stopping,
		pulseMode = $bindable(),
		pulseDuration = $bindable(),
		pulseSpeed = $bindable(),
		pulseDegrees = $bindable(),
		gearRatio,
		onPulse,
		onStop
	}: {
		stepperKey: string;
		keyboardShortcuts: boolean;
		pulsing: Record<string, boolean>;
		homing: boolean;
		canceling: boolean;
		stopping: boolean;
		pulseMode: PulseMode;
		pulseDuration: number;
		pulseSpeed: number;
		pulseDegrees: number;
		gearRatio: number;
		onPulse: (direction: 'cw' | 'ccw') => void;
		onStop: () => void;
	} = $props();
</script>

<!-- Moving a stepper by hand, as one control: the three buttons that move it in
     one outline with a line between them (Stop in the middle, the direction
     that is moving filled with the primary's tint), how far each press goes,
     and how fast. The arrow keys press the outer buttons when shortcuts are on.
     Where the panel is wide the jog control is at the left and what each press
     moves at its right, along one line; narrow, one under the other. -->
<div class="@container flex flex-col gap-4">
	<div class="grid gap-4 @2xl:max-w-4xl @2xl:grid-cols-2 @2xl:items-end @2xl:gap-10">
		<div
			role="group"
			aria-label={$_('components.settings.stepper.stepperPulseControls.jogGroupLabel')}
			class="grid h-(--size-control-lg) grid-cols-[1fr_auto_1fr] divide-x divide-line-strong overflow-hidden rounded-button border border-line-strong bg-field"
		>
			<button
				type="button"
				aria-label={$_('components.settings.stepper.stepperPulseControls.ccwAriaLabel')}
				onclick={() => onPulse('ccw')}
				disabled={Boolean(pulsing[`${stepperKey}:ccw`]) || homing || canceling}
				class="flex h-full items-center justify-center gap-1.5 text-sm font-medium transition-colors focus-visible:-outline-offset-2 disabled:pointer-events-none disabled:opacity-45
					{pulsing[`${stepperKey}:ccw`] ? 'bg-primary-soft text-primary-ink' : 'text-ink hover:bg-hover'}"
			>
				<ChevronLeft size={18} />{$_('components.settings.stepper.stepperPulseControls.ccwText')}
			</button>
			<button
				type="button"
				onclick={onStop}
				disabled={stopping || homing || canceling}
				class="flex h-full items-center justify-center gap-2 px-5 text-sm font-medium text-danger-ink transition-colors hover:bg-danger-soft focus-visible:-outline-offset-2 disabled:pointer-events-none disabled:opacity-45"
			>
				<Square size={12} fill="currentColor" />{$_('components.settings.stepper.stepperPulseControls.stopText')}
			</button>
			<button
				type="button"
				aria-label={$_('components.settings.stepper.stepperPulseControls.cwAriaLabel')}
				onclick={() => onPulse('cw')}
				disabled={Boolean(pulsing[`${stepperKey}:cw`]) || homing || canceling}
				class="flex h-full items-center justify-center gap-1.5 text-sm font-medium transition-colors focus-visible:-outline-offset-2 disabled:pointer-events-none disabled:opacity-45
					{pulsing[`${stepperKey}:cw`] ? 'bg-primary-soft text-primary-ink' : 'text-ink hover:bg-hover'}"
			>
				{$_('components.settings.stepper.stepperPulseControls.cwText')}<ChevronRight size={18} />
			</button>
		</div>

		<div class="flex flex-col gap-3">
			<div class="flex items-center justify-between gap-3">
				<span class="text-sm font-medium text-ink"
					>{$_('components.settings.stepper.stepperPulseControls.eachPressMoves')}</span
				>
				<SegmentedControl
					label={$_('components.settings.stepper.stepperPulseControls.moveByLabel')}
					size="sm"
					bind:value={pulseMode}
					options={[
						{
							value: 'degrees',
							label: $_('components.settings.stepper.stepperPulseControls.moveByDegrees')
						},
						{
							value: 'duration',
							label: $_('components.settings.stepper.stepperPulseControls.moveByTime')
						}
					]}
				/>
			</div>
			<div class="grid grid-cols-2 gap-3">
				{#if pulseMode === 'duration'}
					<Input
						type="number"
						min={0.05}
						max={MAX_PULSE_DURATION_S}
						step={0.05}
						bind:value={pulseDuration}
						unit="s"
						aria-label={$_('components.settings.stepper.stepperPulseControls.durationAriaLabel')}
					/>
				{:else}
					<Input
						type="number"
						min={1}
						max={3600}
						step={1}
						bind:value={pulseDegrees}
						unit="°"
						aria-label={$_('components.settings.stepper.stepperPulseControls.degreesAriaLabel')}
					/>
				{/if}
				<Input
					type="number"
					min={1}
					step={50}
					bind:value={pulseSpeed}
					unit="steps/s"
					aria-label={$_('components.settings.stepper.stepperPulseControls.speedAriaLabel')}
				/>
			</div>
		</div>
	</div>
	{#if keyboardShortcuts}
		<p class="text-sm text-ink-muted">
			{$_('components.settings.stepper.stepperPulseControls.arrowKeysHint')}
		</p>
	{/if}
	{#if pulseMode === 'degrees' && gearRatio !== 1}
		<p class="num text-sm text-ink-muted">
			{$_('components.settings.stepper.stepperPulseControls.gearRatioNote', {
				values: {
					ratio: gearRatio.toFixed(2),
					outputDeg: pulseDegrees,
					motorDeg: (pulseDegrees * gearRatio).toFixed(1)
				}
			})}
		</p>
	{/if}
</div>
