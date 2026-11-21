<script lang="ts">
	import { _ } from 'svelte-i18n';
	import ServoBackendSection from '$lib/components/settings/ServoBackendSection.svelte';
	import Esp32FirmwareSection from '$lib/components/settings/Esp32FirmwareSection.svelte';
	import ServoGateDiagPanel from '$lib/components/settings/ServoGateDiagPanel.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import Tabs from '$lib/components/ui/Tabs.svelte';

	type Tab = 'settings' | 'diagnostics';
	let tab = $state<Tab>('settings');
</script>

<svelte:head><title>{$_('routes.settings.servoControl.pageTitle')}</title></svelte:head>

<PageHeader
	title={$_('routes.settings.servoControl.title')}
	description={$_('routes.settings.servoControl.description')}
/>

<Tabs
	label={$_('routes.settings.servoControl.tabs.ariaLabel')}
	bind:value={tab}
	items={[
		{ value: 'settings', label: $_('routes.settings.servoControl.tabs.settings') },
		{ value: 'diagnostics', label: $_('routes.settings.servoControl.tabs.diagnostics') }
	]}
/>

{#if tab === 'settings'}
	<div class="flex flex-col gap-4">
		<ServoBackendSection />
		<Esp32FirmwareSection />
	</div>
{:else}
	<div class="flex flex-col gap-4">
		<ServoGateDiagPanel />
	</div>
{/if}
