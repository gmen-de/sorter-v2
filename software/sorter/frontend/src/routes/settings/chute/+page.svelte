<script lang="ts">
	import { _ } from 'svelte-i18n';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import StepperPanel from '$lib/components/settings/StepperPanel.svelte';
</script>

<svelte:head><title>{$_('routes.settings.chute.page.pageTitle')}</title></svelte:head>

<PageHeader
	title={$_('routes.settings.chute.page.title')}
	description={$_('routes.settings.chute.page.description')}
/>
<StepperPanel
	stepperKey="chute"
	label={$_('routes.settings.chute.page.stepperLabel')}
	endstop={{
		configEndpoint: '/api/hardware-config/chute',
		liveEndpoint: '/api/hardware-config/chute/live',
		homeEndpoint: '/api/hardware-config/chute/calibrate/find-endstop',
		homeCancelEndpoint: '/api/hardware-config/chute/calibrate/cancel'
	}}
/>
