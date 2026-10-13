import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithRouter } from '../../test/test-utils'
import Workflows from '../Workflows'

vi.mock('../../services/api')

describe('<Workflows />', () => {
  it('renders the JIRA workflow runs header', async () => {
    renderWithRouter(<Workflows />)
    expect(await screen.findByRole('heading', { name: /JIRA workflow runs/i })).toBeInTheDocument()
  })
})
