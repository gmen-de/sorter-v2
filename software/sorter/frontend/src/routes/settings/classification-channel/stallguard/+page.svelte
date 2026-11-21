<script lang="ts">
	import { _ } from 'svelte-i18n';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import StepperStallGuard from '$lib/components/settings/StepperStallGuard.svelte';
</script>

<svelte:head><title>{$_('routes.settings.classificationChannel.stallguard.pageTitle')}</title></svelte:head>

<PageHeader
	title={$_('routes.settings.classificationChannel.stallguard.title')}
	description={$_('routes.settings.classificationChannel.stallguard.description')}
/>
<StepperStallGuard
	motor="carousel"
	label={$_('routes.settings.classificationChannel.stallguard.motorLabel')}
/>
