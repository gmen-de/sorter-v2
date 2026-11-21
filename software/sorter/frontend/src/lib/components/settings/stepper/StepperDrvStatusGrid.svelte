<script lang="ts">
	import { _ } from 'svelte-i18n';

	let {
		drvStatus
	}: {
		drvStatus: Record<string, any>;
	} = $props();
</script>

{#snippet flag(label: string, on: boolean, tone: 'danger' | 'warning')}
	<div class="flex justify-between gap-2">
		<dt class="text-ink-muted">{label}</dt>
		<dd class={on ? (tone === 'danger' ? 'font-medium text-danger-ink' : 'font-medium text-warning-ink') : 'text-ink'}>
			{on
				? $_('components.settings.stepper.stepperDrvStatusGrid.yes')
				: $_('components.settings.stepper.stepperDrvStatusGrid.no')}
		</dd>
	</div>
{/snippet}

<div class="px-(--pad-panel) py-(--pad-row)">
	<div class="label">{$_('components.settings.stepper.stepperDrvStatusGrid.heading')}</div>
	<dl class="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 rounded-control bg-well p-3 text-sm">
		{@render flag($_('components.settings.stepper.stepperDrvStatusGrid.flags.overheated'), drvStatus.ot, 'danger')}
		{@render flag($_('components.settings.stepper.stepperDrvStatusGrid.flags.heatWarning'), drvStatus.otpw, 'warning')}
		{@render flag($_('components.settings.stepper.stepperDrvStatusGrid.flags.shortCoilA'), drvStatus.s2ga, 'danger')}
		{@render flag($_('components.settings.stepper.stepperDrvStatusGrid.flags.shortCoilB'), drvStatus.s2gb, 'danger')}
		{@render flag($_('components.settings.stepper.stepperDrvStatusGrid.flags.openCoilA'), drvStatus.ola, 'warning')}
		{@render flag($_('components.settings.stepper.stepperDrvStatusGrid.flags.openCoilB'), drvStatus.olb, 'warning')}
		<div class="flex justify-between gap-2">
			<dt class="text-ink-muted">StealthChop</dt>
			<dd class="text-ink">
				{drvStatus.stealth
					? $_('components.settings.stepper.stepperDrvStatusGrid.stealthChopActive')
					: $_('components.settings.stepper.stepperDrvStatusGrid.stealthChopOff')}
			</dd>
		</div>
		<div class="flex justify-between gap-2">
			<dt class="text-ink-muted">{$_('components.settings.stepper.stepperDrvStatusGrid.standingStill')}</dt>
			<dd class="text-ink">
				{drvStatus.stst
					? $_('components.settings.stepper.stepperDrvStatusGrid.yes')
					: $_('components.settings.stepper.stepperDrvStatusGrid.no')}
			</dd>
		</div>
		<div class="flex justify-between gap-2">
			<dt class="text-ink-muted">{$_('components.settings.stepper.stepperDrvStatusGrid.current')}</dt>
			<dd class="num text-ink">{drvStatus.cs_actual}</dd>
		</div>
		<div class="flex justify-between gap-2">
			<dt class="text-ink-muted">SG_RESULT</dt>
			<dd class="num text-ink">{drvStatus.sg_result}</dd>
		</div>
		<div
			class="col-span-2 flex justify-between gap-2 {drvStatus.ot
				? 'font-medium text-danger-ink'
				: drvStatus.otpw
					? 'font-medium text-warning-ink'
					: ''}"
		>
			<dt class={drvStatus.ot || drvStatus.otpw ? '' : 'text-ink-muted'}>{$_('components.settings.stepper.stepperDrvStatusGrid.temperature')}</dt>
			<dd class={drvStatus.ot || drvStatus.otpw ? '' : 'text-ink'}>
				{drvStatus.ot
					? $_('components.settings.stepper.stepperDrvStatusGrid.temp.aboveShutdown', { values: { deg: 157 } })
					: drvStatus.t157
						? $_('components.settings.stepper.stepperDrvStatusGrid.temp.above', { values: { deg: 157 } })
						: drvStatus.t150
							? $_('components.settings.stepper.stepperDrvStatusGrid.temp.above', { values: { deg: 150 } })
							: drvStatus.t143
								? $_('components.settings.stepper.stepperDrvStatusGrid.temp.above', { values: { deg: 143 } })
								: drvStatus.t120
									? $_('components.settings.stepper.stepperDrvStatusGrid.temp.above', { values: { deg: 120 } })
									: $_('components.settings.stepper.stepperDrvStatusGrid.temp.below', { values: { deg: 120 } })}
			</dd>
		</div>
	</dl>
</div>
