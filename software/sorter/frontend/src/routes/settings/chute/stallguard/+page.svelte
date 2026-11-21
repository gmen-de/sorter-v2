<script lang="ts">
	import { _ } from 'svelte-i18n';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import StepperStallGuard from '$lib/components/settings/StepperStallGuard.svelte';
	import ChutePositionCheck from '$lib/components/settings/stepper/ChutePositionCheck.svelte';
</script>

<svelte:head><title>{$_('routes.settings.chute.stallguard.pageTitle')}</title></svelte:head>

<PageHeader
	title={$_('routes.settings.chute.stallguard.title')}
	description={$_('routes.settings.chute.stallguard.description')}
/>
<div class="flex min-w-0 flex-col gap-(--gap-panels)">
	<ChutePositionCheck />
	<StepperStallGuard
		motor="chute"
		label={$_('routes.settings.chute.stallguard.motorLabel')}
		operatingSpeedEndpoint="/api/hardware-config/chute"
		stressTestHref="/settings/chute"
	/>
</div>
