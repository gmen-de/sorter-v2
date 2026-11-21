<script lang="ts">
	import { _ } from 'svelte-i18n';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import KeyValue from '$lib/components/ui/KeyValue.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';

	type HiveSetupTarget = {
		id: string;
		name: string;
		url: string;
		machine_id: string | null;
		enabled: boolean;
	};

	let {
		hiveLoading,
		officialHiveTarget,
		defaultHiveUrl,
		hiveUrl = $bindable(),
		hiveConnecting,
		hiveError,
		hiveStatus,
		machineDisplayName,
		onConnect,
		onSkip
	}: {
		hiveLoading: boolean;
		officialHiveTarget: HiveSetupTarget | null;
		defaultHiveUrl: string;
		hiveUrl: string;
		hiveConnecting: boolean;
		hiveError: string | null;
		hiveStatus: string | null;
		machineDisplayName: string;
		onConnect: () => void;
		onSkip: () => void;
	} = $props();
</script>

<div class="flex flex-col gap-(--gap-panels)">
	{#if hiveLoading}
		<Panel>
			<p class="flex items-center gap-2 text-sm text-ink-muted">
				<Spinner size={16} />
				{$_('components.setup.steps.hiveStep.checkingConnection')}
			</p>
		</Panel>
	{:else if officialHiveTarget}
		<Alert tone="success" title={$_('components.setup.steps.hiveStep.connected.title')}>
			{$_('components.setup.steps.hiveStep.connected.registeredWith')}
			<span class="font-mono">{officialHiveTarget.url}</span>.
			{#if officialHiveTarget.machine_id}
				{$_('components.setup.steps.hiveStep.connected.machineIdLabel')}
				<span class="font-mono">{officialHiveTarget.machine_id}</span>.
			{/if}
			{$_('components.setup.steps.hiveStep.connected.manageHint')}
		</Alert>
	{:else}
		<Panel
			title={$_('components.setup.steps.hiveStep.linkPanel.title')}
			description={$_('components.setup.steps.hiveStep.linkPanel.description')}
		>
			<div class="flex max-w-xl flex-col gap-4">
				<Field
					label={$_('components.setup.steps.hiveStep.linkPanel.serverLabel')}
					for="setup-hive-url"
					help={$_('components.setup.steps.hiveStep.linkPanel.serverHelp')}
				>
					<Input
						id="setup-hive-url"
						type="url"
						class="font-mono"
						bind:value={hiveUrl}
						placeholder={defaultHiveUrl}
						disabled={hiveConnecting}
					/>
				</Field>
				<KeyValue
					items={[
						{
							label: $_('components.setup.steps.hiveStep.linkPanel.machineNameLabel'),
							value: machineDisplayName
						}
					]}
				/>
				<p class="text-sm text-ink-muted">
					{$_('components.setup.steps.hiveStep.linkPanel.appearanceHint')}
				</p>
			</div>
			{#snippet footer()}
				<Button variant="ghost" disabled={hiveConnecting} onclick={onSkip}
					>{$_('components.setup.steps.hiveStep.linkPanel.skipButton')}</Button
				>
				<Button
					variant="primary"
					icon={ExternalLink}
					loading={hiveConnecting}
					disabled={!hiveUrl.trim()}
					onclick={onConnect}
				>
					{hiveConnecting
						? $_('components.setup.steps.hiveStep.linkPanel.openingButton')
						: $_('components.setup.steps.hiveStep.linkPanel.continueButton')}
				</Button>
			{/snippet}
		</Panel>
	{/if}

	{#if hiveError}
		<Alert tone="danger" title={$_('components.setup.steps.hiveStep.connectionFailedTitle')}
			>{hiveError}</Alert
		>
	{:else if hiveStatus}
		<Alert tone="success">{hiveStatus}</Alert>
	{/if}
</div>
