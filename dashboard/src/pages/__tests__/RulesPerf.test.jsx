import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithRouter } from '../../test/test-utils'
import RulesPerf from '../RulesPerf'

vi.mock('../../services/api')

describe('<RulesPerf />', () => {
  it('renders the rules performance header', async () => {
    renderWithRouter(<RulesPerf />)
    expect(await screen.findByRole('heading', { name: /Rules performance/i })).toBeInTheDocument()
  })
})
